/** 注册检查日历视图：原生日历 + 局部数据/点击扩展。 */
import { registry } from "@web/core/registry";
import { calendarView } from "@web/views/calendar/calendar_view";
import { InspectionCalendarController } from "./inspection_calendar_controller";
import { InspectionCalendarModel } from "./inspection_calendar_model";
import { InspectionCalendarRenderer } from "./inspection_calendar_renderer";

export const inspectionCalendarView = {
    ...calendarView,
    Controller: InspectionCalendarController,
    Model: InspectionCalendarModel,
    Renderer: InspectionCalendarRenderer,
};

registry.category("views").add("inspection_calendar", inspectionCalendarView);
