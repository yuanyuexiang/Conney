from pydantic import BaseModel

from relay.services.delivery import DRX_OUTPUT_VERSION


class WorkbookTemplate(BaseModel):
    code: str
    name: str
    customer: str
    recognition: str
    required_fields: tuple[str, ...]
    rule_notes: tuple[str, ...]
    output_version: str | None = None
    output_sheets: tuple[str, ...] = ()


WORKBOOK_TEMPLATES = (
    WorkbookTemplate(
        code="drx_delfor",
        name="DRX DELFOR",
        customer="德科 DRX",
        recognition="报文标题以 DRX-DELFOR 开头，并校验报文号、物料块和数量表头。",
        required_fields=("DelforNo", "BuyerItemNo", "poNo", "Qty", "QtyType", "QtyUnit"),
        rule_notes=(
            "按客户料号和采购订单匹配物料，使用供应商料号辅助核验。",
            "已验证的日期点按周归集，完整起止区间保留为区间预测。",
            "未确认的数量代码按数量类型、状态和单位分组待核对。单批结论随草稿保存。",
            "ETA 按草稿锁定的运输时效计算，具备依据的人工 ETA 优先。",
        ),
        output_version=DRX_OUTPUT_VERSION,
        output_sheets=("给生产", "需求与来源"),
    ),
    WorkbookTemplate(
        code="vitesco_delfor",
        name="Vitesco DELFOR",
        customer="舍弗勒 / Vitesco",
        recognition="报文标题以 Vitesco-DELFOR 开头，并校验物料块及数量表。",
        required_fields=(
            "BuyerItemNo",
            "Qty",
            "QtyType",
            "EarliestDeliveryDate",
            "LastDeliveryDate",
        ),
        rule_notes=("按原始交货区间读取预测，保留原单位及显式零值。",),
    ),
    WorkbookTemplate(
        code="jlr_delfor",
        name="JLR DELFOR",
        customer="捷豹路虎",
        recognition="报文标题以 JLR_DELFOR 开头，读取预测交货计划物料块和日期区间。",
        required_fields=("DelforNo", "BuyerItemNo", "poNo", "Qty", "QtyType"),
        rule_notes=("日期点、区间及六位日期原值保留，周需求和累计差分按工作地图验收。",),
    ),
    WorkbookTemplate(
        code="jlr_deljit",
        name="JLR DELJIT",
        customer="捷豹路虎",
        recognition="报文标题以 JLR_DELJIT 开头，读取近期供货报文中的物料块和数量表。",
        required_fields=("DeljitNo", "BuyerItemNo", "poNo", "Qty", "QtyType"),
        rule_notes=("按报文保留日期、累计数量及无日期记录，周内拆量按工作地图验收。",),
    ),
    WorkbookTemplate(
        code="kroschu_4905",
        name="Kroschu 4905",
        customer="科伯舒特",
        recognition="报文标题以 4905 - Delivery Call Off 开头，按采购订单定位物料块。",
        required_fields=(
            "NewTransmissionNo",
            "OrderNo",
            "CustomerItemNo",
            "DateTime",
            "Qty",
            "QuantityUnit",
        ),
        rule_notes=("保留原始交货日期、单位和特殊日期代码，按客户交付样表验收日期轴。",),
    ),
)
