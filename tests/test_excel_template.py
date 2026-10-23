"""固定 Excel 模板列名与 37 字段顺序一致性测试。"""

from __future__ import annotations

from app.data.db import EXCEL_FIELD_NAMES
from app.utils.excel_template import EXCEL_COLUMN_NAMES, EXCEL_HEADER_ROW


EXPECTED_COLUMNS = (
    "毕结业批次",
    "毕业审核",
    "学位审核",
    "学生类型",
    "院号",
    "学位类别码",
    "学位类别全",
    "专业类别",
    "打印标记",
    "证书编号",
    "学位证书编号",
    "考生号",
    "省份代码",
    "学号",
    "姓名",
    "性别",
    "出生日期",
    "证件类型",
    "证件号码",
    "政治面貌",
    "民族",
    "院校代码",
    "院校名称",
    "专业代码",
    "专业名称",
    "分院",
    "学院名",
    "班级",
    "层次",
    "学制",
    "学习形式",
    "当前年级",
    "入学日期",
    "毕业日期",
    "注册状态",
    "备注",
    "其他证书编号",
)


def test_template_has_exactly_the_c2_columns_in_order() -> None:
    assert EXCEL_HEADER_ROW == 1
    assert EXCEL_COLUMN_NAMES == EXPECTED_COLUMNS
    assert len(EXCEL_COLUMN_NAMES) == 37
    assert len(EXCEL_COLUMN_NAMES) == len(EXCEL_FIELD_NAMES)


def test_column_names_align_with_database_field_order() -> None:
    fields = dict(zip(EXCEL_COLUMN_NAMES, EXCEL_FIELD_NAMES, strict=True))

    assert fields["院号"] == "college_number"
    assert fields["学位类别全"] == "degree_category_name"
    assert fields["考生号"] == "candidate_no"
    assert fields["学号"] == "student_no"
    assert fields["证件号码"] == "id_number"
    assert fields["院校代码"] == "institution_code"
    assert fields["院校名称"] == "institution_name"
    assert fields["分院"] == "branch"
    assert fields["学院名"] == "faculty_name"
    assert fields["层次"] == "education_level"
    assert fields["当前年级"] == "current_grade"
    assert fields["其他证书编号"] == "other_certificate_no"
