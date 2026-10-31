/**
 * 检查日历控制器：把“计划”点击接到只读详情对话框。
 *
 * 月/周/日视图由自定义 Renderer 直接调用 onPlanClick；
 * 年视图和日历浮层的“查看”按钮走原生 editRecord，这里再拦截负数 id，
 * 避免用负数 res_id 打开检查单。
 */
import { CalendarController } from "@web/views/calendar/calendar_controller";
import { InspectionCalendarPlanDialog } from "./inspection_calendar_plan_dialog";

export class InspectionCalendarController extends CalendarController {
    setup() {
        super.setup();
        this._baseRendererProps.onPlanClick = this.openPlanDetails.bind(this);
    }

    openPlanDetails(record) {
        this.displayDialog(InspectionCalendarPlanDialog, {
            record: record.rawRecord || {},
        });
    }

    editRecord(record, context = {}) {
        if (record && record.id < 0) {
            this.openPlanDetails(record);
            return Promise.resolve();
        }
        return super.editRecord(record, context);
    }
}
