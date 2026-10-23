"""Service 层异常类型。

Service 只把可预期的业务失败转换为这些异常，供 UI 直接展示；
未预期异常不在此伪装成业务错误。
"""

from __future__ import annotations


class ServiceError(Exception):
    """可向用户展示的业务错误基类。"""


class ValidationError(ServiceError):
    """输入校验失败（学号、日期、字段白名单、分页参数等）。"""


class DuplicateStudentNoError(ServiceError):
    """学号与已有记录重复。"""


class DuplicateValueError(ServiceError):
    """学号或证书编号触发数据库唯一约束。"""


class NumberConflictError(ServiceError):
    """证书编号与已有记录冲突，本次生成已回滚且未消耗流水。"""


class RecordNotFoundError(ServiceError):
    """目标记录不存在或已被删除。"""


class DeleteBlockedError(ServiceError):
    """记录仍被其他业务数据引用，数据库拒绝删除。"""


class AuditWriteError(ServiceError):
    """成功操作因审计日志写入失败而整体回滚。"""


class DataAccessError(ServiceError):
    """意外数据库错误，操作未完成。"""


class PhotoError(ServiceError):
    """照片业务错误基类。"""


class InvalidPhotoError(PhotoError):
    """源文件不存在、格式不符或内容无法作为 JPEG 解码。"""


class PhotoStorageError(PhotoError):
    """照片目录或目标文件写入失败。"""


class PhotoPathError(PhotoError):
    """数据库中的照片相对路径不安全或超出照片目录。"""


class TemplateError(ServiceError):
    """模板业务错误基类。"""


class TemplateValidationError(TemplateError):
    """模板 JSON 结构、字段绑定、几何尺寸或样式取值不合法。"""


class TemplateDataError(TemplateError):
    """数据库中的模板列与 JSON 内容不一致。"""


class TemplateNotFoundError(TemplateError):
    """指定模板或内置模板资源不存在。"""


class TemplateReadOnlyError(TemplateError):
    """试图修改内置只读模板。"""


class TemplateResourceError(TemplateError):
    """内置模板资源缺失、无法读取或内容无效。"""

