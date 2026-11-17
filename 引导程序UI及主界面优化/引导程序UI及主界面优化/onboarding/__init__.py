# -*- coding: utf-8 -*-
"""
苍穹3号 · onboarding（访谈 / 引导）模块

这里创建并导出蓝图，供根目录的「演示版-后端.py」注册。
访谈/引导的具体路由待 interview_backend.py 补全后接入；补全时把蓝图定义
迁到 interview_backend.py，并将下面改为：

    from .interview_backend import onboarding_bp
"""
# -*- coding: utf-8 -*-
"""苍穹3号 · 引导/访谈模块"""
from .interview_backend import interview_bp
# 别名：兼容 onboarding_bp 的导入写法
onboarding_bp = interview_bp

__all__ = ["interview_bp", "onboarding_bp"]


# onboarding_bp = Blueprint(
#     "onboarding",
#     __name__,
#     url_prefix="/onboarding",
# )

# __all__ = ["onboarding_bp"]
