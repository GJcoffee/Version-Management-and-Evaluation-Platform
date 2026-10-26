"""测试包。

CI（``GITHUB_ACTIONS``）、``FORCE_COLOR`` 会让 typer 在导入时强制输出带 ANSI 颜色的帮助文本，断言里的
``--host`` 之类会被转义码切开。typer 在导入时读取该变量，所以必须在任何 evalp / typer 模块导入之前设置。
"""

import os

os.environ.setdefault("_TYPER_FORCE_DISABLE_TERMINAL", "1")
