/**
 * 检查日历渲染扩展：让“计划”（负数 id）点击时弹只读详情，
 * 真实检查保持原生日历行为（弹层 → 打开检查表单）。
 */
import { CalendarCommonRenderer } from "@web/views/calendar/calendar_common/calendar_common_renderer";
import { CalendarRenderer } from "@web/views/calendar/calendar_renderer";

export class InspectionCalendarCommonRenderer extends CalendarCommonRenderer {
    static props = {
        ...CalendarCommonRenderer.props,
        onPlanClick: Function,
    };

    onClick(info) {
        const record = this.props.model.records[info.event.id];
        if (record && record.id < 0) {
            this.props.onPlanClick(record);
            return;
        }
        super.onClick(info);
    }
}

export class InspectionCalendarRenderer extends CalendarRenderer {
    static props = {
        ...CalendarRenderer.props,
        onPlanClick: Function,
    };

    static components = {
        ...CalendarRenderer.components,
        day: InspectionCalendarCommonRenderer,
        week: InspectionCalendarCommonRenderer,
        month: InspectionCalendarCommonRenderer,
    };
}
