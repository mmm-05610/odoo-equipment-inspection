/**
 * 检查日历的局部数据扩展。
 *
 * 原生日历只加载 qc.inspection 的真实检查单；这里在每次加载可见日期范围时，
 * 调用只读 RPC get_cycle_plan_preview() 现算“未来周期计划”，把负数 id 的
 * 伪记录合并到同一份 records 里渲染。浏览不会写任何数据库记录。
 */
import { serializeDate } from "@web/core/l10n/dates";
import { CalendarModel } from "@web/views/calendar/calendar_model";

export class InspectionCalendarModel extends CalendarModel {
    async loadRecords(data) {
        const records = await super.loadRecords(data);
        if (!data.range) {
            return records;
        }
        let preview = { items: [], truncated: false, message: false };
        try {
            preview = await this.orm.call(
                this.meta.resModel,
                "get_cycle_plan_preview",
                [
                    serializeDate(data.range.start),
                    serializeDate(data.range.end),
                    this.meta.domain || [],
                ],
                { context: this.meta.context }
            );
        } catch (error) {
            // 计划预览失败不应阻塞真实检查日历。
            this.notification.add(
                error.message || "无法加载未来检查计划，暂时只显示真实检查。",
                { type: "warning" }
            );
            return records;
        }
        for (const rawRecord of preview.items || []) {
            records[rawRecord.id] = this.normalizeRecord(rawRecord);
        }
        if (preview.message) {
            this.notification.add(preview.message, { type: "warning" });
        }
        return records;
    }
}
