"""The Job360 browser agent (docs/product/BROWSER_AGENT.md, VISION decision 29).

A cloud Chromium the user's own assistant drives over the MCP ``browser``
tool, laptop on or off.

Submodules:
    safety.py  — the hard lines as pure functions (password box, final
                 submit, CAPTCHA / sign-in wall, the SSRF screen).
    egress.py  — the proxy every browser request goes through, which screens
                 each target host before it connects.
"""
