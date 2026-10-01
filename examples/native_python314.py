"""Native PEP 750 example. This file requires Python 3.14 or newer."""

from template_strings_demo import parameterize_sql, render_html

name = "<Admin>"
print(render_html(t"<h1>Hello {name}</h1>"))

user_id = "7 OR 1=1"
query, parameters = parameterize_sql(t"SELECT * FROM users WHERE id = {user_id}")
print(query, parameters)
