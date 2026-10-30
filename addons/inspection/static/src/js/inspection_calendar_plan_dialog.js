/**
 * “未来检查计划”只读详情：明确告诉用户这是计划、尚未生成检查单，
 * 不提供任何生成/新建入口。
 */
import { Dialog } from "@web/core/dialog/dialog";
import { Component } from "@odoo/owl";

export class InspectionCalendarPlanDialog extends Component {
    static template = "inspection.InspectionCalendarPlanDialog";
    static components = { Dialog };
    static props = {
        close: Function,
        record: Object,
    };

    get planDate() {
        return this.props.record.inspection_date || "";
    }

    get equipmentName() {
        return this._many2oneLabel(this.props.record.equipment_id);
    }

    get testName() {
        return this._many2oneLabel(this.props.record.test);
    }

    get inspectorName() {
        return this._many2oneLabel(this.props.record.user);
    }

    get cycleLabel() {
        return this.props.record.inspection_calendar_cycle_label || "";
    }

    _many2oneLabel(value) {
        if (Array.isArray(value)) {
            return value[1];
        }
        return value || "";
    }
}
