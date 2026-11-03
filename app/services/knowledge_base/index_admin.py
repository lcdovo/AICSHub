"""旧管理Mixin导入兼容；不再维护MySQL/SQLite手写DDL或按标题删除路径。"""
from .lifecycle import LifecycleMixin


class IndexAdminMixin(LifecycleMixin):
    pass
