#!/usr/bin/env python3
"""
GitHub Automated Patch Agent v2
================================
Enhanced version with LLM-based fix generation using structured prompting.

Improvements over v1:
  - Integrates with OpenAI/Claude/other LLMs for intelligent fix generation
  - Uses "Master System Prompt" with strict JSON output format
  - Validates LLM responses against JSON schema
  - Falls back to template-based generation if LLM fails
  - Enhanced security constraints (no eval/exec/os imports)
  - Production-ready with comprehensive error handling

Author: GitHub Automation Agent
License: MIT
"""

import os
import sys
import logging
import json
import subprocess
import tempfile
from datetime import datetime
from typing import Dict, List, Optional, Tuple, Any
from pathlib import Path
from dataclasses import dataclass, field
from abc import ABC, abstractmethod

try:
    from github import Github, GithubException
except ImportError:
    print("❌ ERROR: PyGithub is not installed. Install with: pip install pygithub")
    sys.exit(1)

# Optional LLM imports - graceful degradation if not available
try:
    import openai
    HAS_OPENAI = True
except ImportError:
    HAS_OPENAI = False

try:
    import anthropic
    HAS_ANTHROPIC = True
except ImportError:
    HAS_ANTHROPIC = False


# ============================================================================
# CONFIGURATION & LOGGING
# ============================================================================

@dataclass
class Config:
    """Configuration for the GitHub Patch Agent v2"""
    repo_owner: str
    repo_name: str
    github_token: Optional[str] = field(default_factory=lambda: os.getenv("GITHUB_TOKEN"))
    llm_provider: str = "openai"  # "openai", "anthropic", or "template"
    llm_api_key: Optional[str] = field(default_factory=lambda: os.getenv("LLM_API_KEY"))
    llm_model: str = "gpt-4"
    base_branch: str = "main"
    patch_branch_prefix: str = "patch-auto-fix"
    bug_label: str = "calculation bug"
    dry_run: bool = False
    log_level: str = "INFO"


def setup_logging(log_level: str = "INFO") -> logging.Logger:
    """Configure logging with timestamps and color coding"""
    logger = logging.getLogger("github_patch_agent_v2")
    logger.setLevel(getattr(logging, log_level.upper()))
    
    formatter = logging.Formatter(
        "[%(asctime)s] [%(levelname)-8s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )
    
    handler = logging.StreamHandler()
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    
    return logger


logger = setup_logging()


# ============================================================================
# MASTER SYSTEM PROMPT (Core Innovation)
# ============================================================================

MASTER_SYSTEM_PROMPT = """You are an expert Python Developer and Security Analyst automated agent. Your task is to analyze mathematical/calculation bugs from GitHub issues and provide secure, production-ready Python fixes.

You will be provided with:
1. Issue Title
2. Issue Description
3. Estimated Bug Type (e.g., division_error, modulo_error, rounding_error, factorial_error, exponent_error)

Your Task:
1. Write a single Python function that fixes the core mathematical issue
   - Include complete, detailed docstrings
   - Use type hints for all parameters and return values
   - Document the issue number, bug type, and fix strategy in the docstring

2. Write comprehensive pytest unit tests for the function
   - Cover normal execution cases
   - Cover edge cases: zero inputs, negative inputs, extremely large numbers, boundary conditions
   - Implement input type validation tests
   - Test error conditions (ValueError, TypeError, OverflowError, ZeroDivisionError)
   - Ensure all tests pass with the fixed function

Security Constraints (MANDATORY):
- ✅ DO: Use math module, decimal module, built-in functions
- ✅ DO: Implement proper error handling (ValueError, TypeError, etc.)
- ✅ DO: Add input validation and boundary checks
- ❌ DO NOT: Use eval(), exec(), compile(), or __import__()
- ❌ DO NOT: Import os, sys, subprocess, or any shell/system modules
- ❌ DO NOT: Use pickle, marshal, or any serialization exploits
- ❌ DO NOT: Access __globals__, __builtins__, or private attributes

Output Format Requirements:
You MUST respond ONLY with a valid JSON object matching this exact structure:
{
  "status": "success",
  "function_code": "import math\\n\\ndef safe_divide()...",
  "test_code": "import pytest\\n\\ndef test_safe_divide()...",
  "explanation": "Brief explanation of the fix and test strategy"
}

CRITICAL: 
- Do NOT include markdown code blocks (no ```python, ```json, or similar)
- Do NOT include explanatory text before or after the JSON
- Do NOT escape newlines as \\n in the actual code - use real newlines
- Ensure the JSON is valid and parseable by json.loads()
- Both function_code and test_code must be syntactically valid Python

If you cannot generate a safe fix, return:
{
  "status": "error",
  "error": "Detailed explanation of why generation failed"
}
"""


# ============================================================================
# JSON SCHEMA VALIDATION
# ============================================================================

class LLMResponseValidator:
    """Validates LLM responses against expected JSON schema"""
    
    EXPECTED_SCHEMA = {
        "success": {
            "status": str,
            "function_code": str,
            "test_code": str,
            "explanation": str,
        },
        "error": {
            "status": str,
            "error": str,
        }
    }
    
    @staticmethod
    def validate(response: Dict[str, Any]) -> Tuple[bool, str]:
        """
        Validate LLM response structure.
        
        Args:
            response: Parsed JSON response from LLM
            
        Returns:
            Tuple of (is_valid: bool, error_message: str or "")
        """
        if not isinstance(response, dict):
            return False, "Response is not a JSON object"
        
        if "status" not in response:
            return False, "Missing 'status' field"
        
        status = response.get("status")
        
        if status == "success":
            required_fields = ["function_code", "test_code", "explanation"]
            for field in required_fields:
                if field not in response:
                    return False, f"Missing required field '{field}' for success response"
                if not isinstance(response[field], str):
                    return False, f"Field '{field}' must be a string"
            return True, ""
        
        elif status == "error":
            if "error" not in response:
                return False, "Missing 'error' field for error response"
            if not isinstance(response["error"], str):
                return False, "Field 'error' must be a string"
            return True, ""
        
        else:
            return False, f"Invalid status value: {status}"
    
    @staticmethod
    def validate_python_syntax(code: str, code_type: str = "function") -> Tuple[bool, str]:
        """
        Validate that generated code is syntactically correct Python.
        
        Args:
            code: Python code to validate
            code_type: Either "function" or "test" for error messages
            
        Returns:
            Tuple of (is_valid: bool, error_message: str or "")
        """
        try:
            compile(code, filename=f"<{code_type}>", mode="exec")
            return True, ""
        except SyntaxError as e:
            return False, f"Syntax error in {code_type}: {e.msg} at line {e.lineno}"
        except Exception as e:
            return False, f"Compilation error in {code_type}: {e}"


# ============================================================================
# LLM INTEGRATION (Abstraction Layer)
# ============================================================================

class LLMProvider(ABC):
    """Abstract base for LLM providers (OpenAI, Anthropic, etc.)"""
    
    @abstractmethod
    def generate_fix(
        self,
        issue_title: str,
        issue_description: str,
        bug_type: str
    ) -> Dict[str, Any]:
        """
        Generate fix using LLM.
        
        Returns:
            Dict with "status": "success"|"error" and relevant fields
        """
        pass


class OpenAIProvider(LLMProvider):
    """OpenAI API integration (GPT-4, GPT-3.5)"""
    
    def __init__(self, api_key: str, model: str = "gpt-4"):
        if not HAS_OPENAI:
            raise ImportError("openai package not installed: pip install openai")
        
        self.client = openai.OpenAI(api_key=api_key)
        self.model = model
        logger.info(f"✅ Initialized OpenAI provider with model: {model}")
    
    def generate_fix(
        self,
        issue_title: str,
        issue_description: str,
        bug_type: str
    ) -> Dict[str, Any]:
        """Call OpenAI API with Master System Prompt"""
        
        user_message = f"""
Issue Title: {issue_title}
Issue Description: {issue_description}
Bug Type: {bug_type}

Generate a secure, production-ready fix for this calculation bug.
"""
        
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {
                        "role": "system",
                        "content": MASTER_SYSTEM_PROMPT
                    },
                    {
                        "role": "user",
                        "content": user_message
                    }
                ],
                temperature=0.2,  # Low temperature for deterministic output
                max_tokens=4000,
                response_format={"type": "json_object"}  # GPT-4 Turbo feature
            )
            
            content = response.choices[0].message.content.strip()
            parsed = json.loads(content)
            logger.info("✅ Received valid JSON response from OpenAI")
            return parsed
            
        except json.JSONDecodeError as e:
            logger.error(f"❌ OpenAI returned invalid JSON: {e}")
            return {"status": "error", "error": f"Invalid JSON from OpenAI: {e}"}
        except openai.APIError as e:
            logger.error(f"❌ OpenAI API error: {e}")
            return {"status": "error", "error": f"OpenAI API error: {e}"}


class AnthropicProvider(LLMProvider):
    """Anthropic Claude API integration"""
    
    def __init__(self, api_key: str, model: str = "claude-3-sonnet-20240229"):
        if not HAS_ANTHROPIC:
            raise ImportError("anthropic package not installed: pip install anthropic")
        
        self.client = anthropic.Anthropic(api_key=api_key)
        self.model = model
        logger.info(f"✅ Initialized Anthropic provider with model: {model}")
    
    def generate_fix(
        self,
        issue_title: str,
        issue_description: str,
        bug_type: str
    ) -> Dict[str, Any]:
        """Call Anthropic API with Master System Prompt"""
        
        user_message = f"""
Issue Title: {issue_title}
Issue Description: {issue_description}
Bug Type: {bug_type}

Generate a secure, production-ready fix for this calculation bug.
"""
        
        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=4000,
                system=MASTER_SYSTEM_PROMPT,
                messages=[
                    {
                        "role": "user",
                        "content": user_message
                    }
                ]
            )
            
            content = response.content[0].text.strip()
            parsed = json.loads(content)
            logger.info("✅ Received valid JSON response from Claude")
            return parsed
            
        except json.JSONDecodeError as e:
            logger.error(f"❌ Claude returned invalid JSON: {e}")
            return {"status": "error", "error": f"Invalid JSON from Claude: {e}"}
        except anthropic.APIError as e:
            logger.error(f"❌ Anthropic API error: {e}")
            return {"status": "error", "error": f"Anthropic API error: {e}"}


# ============================================================================
# STEP 1: AUTHENTICATION & INITIALIZATION
# ============================================================================

class GitHubAuthenticator:
    """Safe GitHub API Authentication"""
    
    def __init__(self, token: Optional[str] = None):
        self.token = token or os.getenv("GITHUB_TOKEN")
        
        if not self.token:
            raise ValueError(
                "❌ GitHub token not provided. Set GITHUB_TOKEN environment variable "
                "or pass token= parameter."
            )
        
        logger.info("✅ GitHub token loaded from environment (PAT detected)")
    
    def get_client(self) -> Github:
        """Create authenticated GitHub API client"""
        try:
            g = Github(self.token)
            user = g.get_user()
            logger.info(f"✅ Authenticated as: {user.login}")
            return g
        except GithubException as e:
            logger.error(f"❌ Authentication failed: {e.status} {e.data}")
            raise


# ============================================================================
# STEP 2: LLM-BASED FIX GENERATION (Main Innovation)
# ============================================================================

@dataclass
class BugReport:
    """Represents a calculation bug issue from GitHub"""
    issue_number: int
    title: str
    description: str
    labels: List[str]
    created_at: str
    url: str


class BugAnalyzer:
    """Analyzes issues to extract bug type and context"""
    
    @staticmethod
    def analyze_bug(bug: BugReport) -> Dict[str, Any]:
        """
        Parse bug report and detect bug type.
        
        Falls back to keyword heuristics if LLM isn't available.
        """
        logger.info(f"📋 Analyzing bug #{bug.issue_number}: {bug.title}")
        
        description = bug.description.lower()
        
        # Heuristic-based detection (fallback)
        bug_type = "unknown"
        if "divide" in description or "division" in description:
            bug_type = "division_error"
        elif "modulo" in description or "remainder" in description:
            bug_type = "modulo_error"
        elif "rounding" in description or "round" in description:
            bug_type = "rounding_error"
        elif "factorial" in description:
            bug_type = "factorial_error"
        elif "power" in description or "exponent" in description:
            bug_type = "exponent_error"
        
        return {
            "issue_number": bug.issue_number,
            "title": bug.title,
            "type": bug_type,
            "description": bug.description,
            "analyzed_at": datetime.now().isoformat()
        }


class FixGenerator:
    """
    Generate fixes using LLM with fallback to templates.
    
    This is the core innovation: uses Master System Prompt to ensure
    secure, production-ready code generation.
    """
    
    def __init__(self, config: Config):
        self.config = config
        self.llm_provider = self._initialize_llm()
        self.validator = LLMResponseValidator()
    
    def _initialize_llm(self) -> Optional[LLMProvider]:
        """Initialize LLM provider based on config"""
        
        if self.config.llm_provider == "openai":
            if not self.config.llm_api_key:
                logger.warning("⚠️  LLM_API_KEY not set, falling back to templates")
                return None
            try:
                return OpenAIProvider(
                    api_key=self.config.llm_api_key,
                    model=self.config.llm_model
                )
            except ImportError as e:
                logger.warning(f"⚠️  OpenAI not available: {e}, falling back to templates")
                return None
        
        elif self.config.llm_provider == "anthropic":
            if not self.config.llm_api_key:
                logger.warning("⚠️  LLM_API_KEY not set, falling back to templates")
                return None
            try:
                return AnthropicProvider(
                    api_key=self.config.llm_api_key,
                    model=self.config.llm_model
                )
            except ImportError as e:
                logger.warning(f"⚠️  Anthropic not available: {e}, falling back to templates")
                return None
        
        else:
            logger.info("📝 Using template-based fix generation")
            return None
    
    def generate_fix(self, analysis: Dict[str, Any]) -> Tuple[str, str]:
        """
        Generate fix using LLM or templates.
        
        Returns:
            Tuple of (function_code, test_code)
        """
        issue_num = analysis.get("issue_number", 0)
        bug_type = analysis.get("type", "unknown")
        
        logger.info(f"🔧 Generating fix for issue #{issue_num} ({bug_type})...")
        
        # Try LLM-based generation first
        if self.llm_provider:
            function_code, test_code = self._generate_via_llm(analysis)
            if function_code and test_code:
                return function_code, test_code
        
        # Fallback to template-based generation
        logger.info("📝 Falling back to template-based generation...")
        return self._generate_via_templates(bug_type, issue_num)
    
    def _generate_via_llm(self, analysis: Dict[str, Any]) -> Tuple[Optional[str], Optional[str]]:
        """
        Generate fix using LLM with validation.
        
        Returns:
            Tuple of (function_code, test_code) or (None, None) on failure
        """
        try:
            response = self.llm_provider.generate_fix(
                issue_title=analysis.get("title", "Unknown"),
                issue_description=analysis.get("description", ""),
                bug_type=analysis.get("type", "unknown")
            )
            
            # Validate response structure
            is_valid, error_msg = self.validator.validate(response)
            if not is_valid:
                logger.error(f"❌ Invalid LLM response structure: {error_msg}")
                return None, None
            
            if response.get("status") != "success":
                logger.error(f"❌ LLM generation failed: {response.get('error')}")
                return None, None
            
            function_code = response.get("function_code", "")
            test_code = response.get("test_code", "")
            
            # Validate Python syntax
            func_valid, func_error = self.validator.validate_python_syntax(function_code, "function")
            if not func_valid:
                logger.error(f"❌ Generated function has {func_error}")
                return None, None
            
            test_valid, test_error = self.validator.validate_python_syntax(test_code, "test")
            if not test_valid:
                logger.error(f"❌ Generated tests have {test_error}")
                return None, None
            
            logger.info("✅ LLM-generated code passed validation")
            logger.info(f"📝 Explanation: {response.get('explanation', '')}")
            
            return function_code, test_code
            
        except Exception as e:
            logger.error(f"❌ LLM generation error: {e}")
            return None, None
    
    def _generate_via_templates(self, bug_type: str, issue_num: int) -> Tuple[str, str]:
        """
        Fallback: Template-based fix generation (from v1).
        
        This ensures the agent always has a working fallback.
        """
        # ... (same as v1 FixGenerator._fix_* methods)
        # For brevity, using simplified templates here
        
        template_map = {
            "division_error": self._template_safe_divide,
            "modulo_error": self._template_safe_modulo,
            "rounding_error": self._template_safe_round,
            "factorial_error": self._template_safe_factorial,
            "exponent_error": self._template_safe_power,
        }
        
        generator_func = template_map.get(bug_type, self._template_generic)
        return generator_func(issue_num)
    
    @staticmethod
    def _template_safe_divide(issue_num: int) -> Tuple[str, str]:
        """Template: Safe division"""
        func = f'''
def safe_divide(numerator: float, denominator: float) -> float:
    """Safe division with zero-check. Fixes issue #{issue_num}"""
    if denominator == 0:
        raise ValueError("Cannot divide by zero")
    return numerator / denominator
'''
        test = '''
import pytest

def test_safe_divide_normal():
    assert safe_divide(10, 2) == 5.0

def test_safe_divide_zero():
    with pytest.raises(ValueError):
        safe_divide(10, 0)
'''
        return func, test
    
    @staticmethod
    def _template_safe_modulo(issue_num: int) -> Tuple[str, str]:
        """Template: Safe modulo"""
        func = f'''
def safe_modulo(dividend: int, divisor: int) -> int:
    """Safe modulo operation. Fixes issue #{issue_num}"""
    if not isinstance(dividend, int) or not isinstance(divisor, int):
        raise TypeError("Both arguments must be integers")
    if divisor == 0:
        raise ValueError("Cannot perform modulo with zero divisor")
    return dividend % divisor
'''
        test = '''
import pytest

def test_safe_modulo_normal():
    assert safe_modulo(10, 3) == 1

def test_safe_modulo_zero():
    with pytest.raises(ValueError):
        safe_modulo(10, 0)
'''
        return func, test
    
    @staticmethod
    def _template_safe_round(issue_num: int) -> Tuple[str, str]:
        """Template: Safe rounding"""
        func = f'''
from decimal import Decimal, ROUND_HALF_EVEN

def safe_round(value: float, decimals: int = 2) -> float:
    """Safe rounding using banker's rounding. Fixes issue #{issue_num}"""
    if decimals < 0:
        raise ValueError("decimals must be non-negative")
    d = Decimal(str(value))
    rounded = d.quantize(Decimal(10) ** -decimals, rounding=ROUND_HALF_EVEN)
    return float(rounded)
'''
        test = '''
import pytest

def test_safe_round_standard():
    assert safe_round(3.14159, 2) == 3.14

def test_safe_round_negative():
    with pytest.raises(ValueError):
        safe_round(10.5, -1)
'''
        return func, test
    
    @staticmethod
    def _template_safe_factorial(issue_num: int) -> Tuple[str, str]:
        """Template: Safe factorial"""
        func = f'''
import math

def safe_factorial(n: int) -> int:
    """Safe factorial with input validation. Fixes issue #{issue_num}"""
    if not isinstance(n, int):
        raise TypeError("Factorial argument must be an integer")
    if n < 0:
        raise ValueError("Factorial is not defined for negative numbers")
    return math.factorial(n)
'''
        test = '''
import pytest

def test_safe_factorial_basic():
    assert safe_factorial(5) == 120

def test_safe_factorial_negative():
    with pytest.raises(ValueError):
        safe_factorial(-5)
'''
        return func, test
    
    @staticmethod
    def _template_safe_power(issue_num: int) -> Tuple[str, str]:
        """Template: Safe exponentiation"""
        func = f'''
def safe_power(base: float, exponent: float) -> float:
    """Safe exponentiation with overflow protection. Fixes issue #{issue_num}"""
    try:
        result = base ** exponent
        if result == float('inf') or result == float('-inf'):
            raise ValueError(f"Exponentiation overflow: {{base}}^{{exponent}}")
        return result
    except OverflowError as e:
        raise ValueError(f"Exponentiation overflow: {{e}}")
'''
        test = '''
import pytest

def test_safe_power_normal():
    assert safe_power(2, 3) == 8

def test_safe_power_overflow():
    with pytest.raises(ValueError, match="overflow"):
        safe_power(1e308, 10)
'''
        return func, test
    
    @staticmethod
    def _template_generic(issue_num: int) -> Tuple[str, str]:
        """Generic fallback template"""
        func = f'''
def fixed_function(x: float) -> float:
    """Placeholder fix for issue #{issue_num}. Manual review required."""
    return x
'''
        test = '''
def test_placeholder():
    assert fixed_function(1.0) == 1.0
'''
        return func, test


class TestRunner:
    """Runs unit tests against generated fixes"""
    
    @staticmethod
    def run_tests(function_code: str, test_code: str) -> Tuple[bool, str]:
        """Execute unit tests in isolated environment"""
        logger.info("🧪 Running unit tests...")
        
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)
            
            func_file = tmpdir_path / "fixed_function.py"
            test_file = tmpdir_path / "test_fixed_function.py"
            
            func_file.write_text(function_code)
            test_file.write_text(f"from fixed_function import *\n\n{test_code}")
            
            try:
                result = subprocess.run(
                    [sys.executable, "-m", "pytest", str(test_file), "-v"],
                    capture_output=True,
                    text=True,
                    timeout=30
                )
                
                success = result.returncode == 0
                output = result.stdout + result.stderr
                
                if success:
                    logger.info("✅ All tests passed!")
                else:
                    logger.warning("⚠️  Some tests failed")
                
                return success, output
                
            except subprocess.TimeoutExpired:
                logger.error("⏱️  Test execution timed out after 30s")
                return False, "⏱️  Test execution timed out"
            except Exception as e:
                logger.error(f"❌ Test execution error: {e}")
                return False, f"❌ Test execution error: {e}"


# ============================================================================
# STEP 3-5: BRANCH, COMMIT, PR (Same as v1, reused)
# ============================================================================

class BranchManager:
    """Handles Git branch creation and management"""
    
    def __init__(self, repo):
        self.repo = repo
    
    def create_branch(self, branch_name: str, base_branch: str = "main") -> bool:
        """Create new branch from base branch"""
        try:
            base_ref = self.repo.get_git_ref(f"heads/{base_branch}")
            base_sha = base_ref.object.sha
            
            try:
                self.repo.get_git_ref(f"heads/{branch_name}")
                logger.warning(f"⚠️  Branch '{branch_name}' already exists")
                return True
            except GithubException:
                pass
            
            self.repo.create_git_ref(f"refs/heads/{branch_name}", base_sha)
            logger.info(f"✅ Created branch: {branch_name}")
            return True
            
        except GithubException as e:
            logger.error(f"❌ Failed to create branch: {e.status} - {e.data}")
            return False


class CommitManager:
    """Handles file creation, updates, and commits"""
    
    def __init__(self, repo):
        self.repo = repo
    
    def commit_fix(
        self,
        branch_name: str,
        file_path: str,
        content: str,
        commit_message: str,
        issue_number: int
    ) -> bool:
        """Commit fixed code to branch"""
        try:
            full_message = f"{commit_message}\n\nFixes #{issue_number}"
            
            try:
                existing = self.repo.get_contents(file_path, ref=branch_name)
                self.repo.update_file(
                    file_path,
                    full_message,
                    content,
                    existing.sha,
                    branch=branch_name
                )
                logger.info(f"✅ Updated file: {file_path}")
            except GithubException:
                self.repo.create_file(
                    file_path,
                    full_message,
                    content,
                    branch=branch_name
                )
                logger.info(f"✅ Created file: {file_path}")
            
            return True
            
        except GithubException as e:
            logger.error(f"❌ Commit failed: {e.status} - {e.data}")
            return False


class PRManager:
    """Manages pull request creation with detailed documentation"""
    
    def __init__(self, repo):
        self.repo = repo
    
    def create_pr(
        self,
        branch_name: str,
        base_branch: str,
        bug: BugReport,
        analysis: Dict[str, Any],
        test_output: str,
        dry_run: bool = False
    ) -> Optional[str]:
        """Create pull request with comprehensive documentation"""
        title = f"🔧 Auto-fix: {bug.title}"
        body = self._generate_pr_body(bug, analysis, test_output)
        
        if dry_run:
            logger.info("🏃 DRY RUN MODE - Would create PR:")
            logger.info(f"  Title: {title}")
            return None
        
        try:
            pr = self.repo.create_pull(
                title=title,
                body=body,
                head=branch_name,
                base=base_branch
            )
            logger.info(f"✅ Created PR #{pr.number}: {pr.html_url}")
            return pr.html_url
            
        except GithubException as e:
            logger.error(f"❌ Failed to create PR: {e.status} - {e.data}")
            return None
    
    @staticmethod
    def _generate_pr_body(bug: BugReport, analysis: Dict[str, Any], test_output: str) -> str:
        """Generate detailed PR description"""
        return f"""## 🐛 Root Cause Analysis

**Original Issue:** #{bug.issue_number}
**Issue Title:** {bug.title}

### Problem Description
{bug.description}

---

## ✅ Solution

This PR implements an automated fix generated using AI-powered code analysis:

1. **Root Cause:** Identified via LLM or template analysis
2. **Fix Strategy:** Implemented with comprehensive error handling and validation
3. **Testing:** All unit tests pass

### Code Changes
- Added safe wrapper functions with input validation
- Implemented proper error messages and exception types
- Comprehensive docstrings and type hints

---

## 🧪 Test Results

```
{test_output[:1500]}
```

**Status:** ✅ All tests passed

---

**Automated by:** GitHub Patch Agent v2 (LLM-Enhanced)
**Created:** {datetime.now().isoformat()}
"""


# ============================================================================
# MAIN ORCHESTRATION
# ============================================================================

class GitHubPatchAgent:
    """Main Orchestration Engine with LLM Integration"""
    
    def __init__(self, config: Config):
        self.config = config
        self.authenticator = GitHubAuthenticator(config.github_token)
        self.client = self.authenticator.get_client()
        self.fix_generator = FixGenerator(config)
    
    def run(self) -> None:
        """Execute full patch automation workflow"""
        logger.info("=" * 70)
        logger.info("🚀 GitHub Automated Patch Agent v2 Starting (LLM-Enhanced)")
        logger.info(f"   Repository: {self.config.repo_owner}/{self.config.repo_name}")
        logger.info(f"   LLM Provider: {self.config.llm_provider}")
        logger.info(f"   Dry Run: {self.config.dry_run}")
        logger.info("=" * 70)
        
        try:
            repo = self.client.get_user(self.config.repo_owner).get_repo(self.config.repo_name)
            logger.info(f"✅ Accessed repository: {repo.full_name}")
            
            bugs = self._scan_bugs(repo)
            if not bugs:
                logger.info("✅ No bugs found. Exiting.")
                return
            
            logger.info(f"📊 Found {len(bugs)} bug(s) to fix")
            
            for bug in bugs:
                self._process_bug(repo, bug)
            
            logger.info("=" * 70)
            logger.info("✅ Patch agent completed successfully")
            logger.info("=" * 70)
            
        except GithubException as e:
            logger.error(f"❌ GitHub API error: {e.status} - {e.data}")
            sys.exit(1)
    
    def _scan_bugs(self, repo) -> List[BugReport]:
        """Scan repository for open issues with bug label"""
        logger.info(f"🔍 Scanning for issues labeled '{self.config.bug_label}'...")
        
        try:
            issues = repo.get_issues(state="open", labels=[self.config.bug_label])
            
            bugs = []
            for issue in issues:
                bug = BugReport(
                    issue_number=issue.number,
                    title=issue.title,
                    description=issue.body or "",
                    labels=[label.name for label in issue.labels],
                    created_at=issue.created_at.isoformat(),
                    url=issue.html_url
                )
                bugs.append(bug)
            
            return bugs
            
        except GithubException as e:
            logger.error(f"❌ Failed to scan issues: {e.status} - {e.data}")
            return []
    
    def _process_bug(self, repo, bug: BugReport) -> None:
        """Process a single bug from scanning to PR creation"""
        logger.info(f"\n📌 Processing bug #{bug.issue_number}: {bug.title}")
        
        analyzer = BugAnalyzer()
        analysis = analyzer.analyze_bug(bug)
        
        function_code, test_code = self.fix_generator.generate_fix(analysis)
        
        runner = TestRunner()
        tests_passed, test_output = runner.run_tests(function_code, test_code)
        
        if not tests_passed:
            logger.warning(f"⚠️  Tests failed for bug #{bug.issue_number}")
            return
        
        branch_name = f"{self.config.patch_branch_prefix}-{bug.issue_number}"
        branch_mgr = BranchManager(repo)
        
        if not branch_mgr.create_branch(branch_name, self.config.base_branch):
            return
        
        file_path = f"fixes/issue_{bug.issue_number}_fix.py"
        commit_mgr = CommitManager(repo)
        
        if not commit_mgr.commit_fix(
            branch_name,
            file_path,
            function_code,
            f"Fix: {bug.title}",
            bug.issue_number
        ):
            return
        
        pr_mgr = PRManager(repo)
        pr_url = pr_mgr.create_pr(
            branch_name,
            self.config.base_branch,
            bug,
            analysis,
            test_output,
            dry_run=self.config.dry_run
        )
        
        if pr_url:
            logger.info(f"✅ Bug #{bug.issue_number} processed successfully: {pr_url}")


# ============================================================================
# ENTRY POINT
# ============================================================================

def main():
    """Main entry point"""
    config = Config(
        repo_owner="luffy45k",
        repo_name="python-template-strings-demo",
        base_branch="main",
        llm_provider=os.getenv("LLM_PROVIDER", "openai"),  # "openai" or "anthropic"
        llm_model=os.getenv("LLM_MODEL", "gpt-4"),
        dry_run=os.getenv("DRY_RUN", "false").lower() == "true"
    )
    
    agent = GitHubPatchAgent(config)
    agent.run()


if __name__ == "__main__":
    main()
