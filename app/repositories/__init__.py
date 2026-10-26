"""Repository 数据访问层。

对外暴露 7 个业务表对应的 Repository；表格结构由 ``app.data.db`` 统一管理。
"""

from app.repositories.app_settings import AppSettingsRepository
from app.repositories.base import Repository
from app.repositories.certificate_records import CertificateRecordRepository
from app.repositories.certificate_templates import CertificateTemplateRepository
from app.repositories.import_batches import ImportBatchRepository
from app.repositories.numbering_rules import NumberingRuleRepository
from app.repositories.operation_logs import OperationLogRepository
from app.repositories.print_records import PrintRecordRepository

__all__ = [
    "AppSettingsRepository",
    "CertificateRecordRepository",
    "CertificateTemplateRepository",
    "ImportBatchRepository",
    "NumberingRuleRepository",
    "OperationLogRepository",
    "PrintRecordRepository",
    "Repository",
]
