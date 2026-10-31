# License AGPL-3.0-or-later.
"""Native ``mail.activity`` todos for equipment inspections.

This module deliberately reuses Odoo's own reminder engine instead of building
a second notification system:

* the todo lives on ``maintenance.equipment``: its form/chatter and its kanban
  already render native activities (``<chatter/>``, ``activity_ids``);
* native activities only know ``res_model``/``res_id`` (here: the equipment),
  so the minimal extra field ``mail.activity.inspection_id`` links every auto
  activity to exactly one inspection.  That field is only set by this module,
  never guessed from a title, a note or the equipment id;
* ``qc.inspection`` keeps that single activity in sync through its lifecycle:
  generated (manual/batch/cycle) -> assigned to the inspector, reassigned ->
  new responsible, success/failed/canceled -> activity closed, waiting ->
  the same activity becomes "waiting for supervisor confirmation" (the
  supervisor snapshot taken on the inspection), draft deletion -> activity
  removed;
* completing the activity cannot bypass the inspection workflow: the native
  completion entry points are intercepted while the inspection is still open;
* ``qc.inspection._sync_inspection_activities()`` is the once-only, repeatable
  backfill entry point for inspections that already existed before this
  feature (called from post_init / migration scripts).
"""
import logging

from markupsafe import Markup, escape

from odoo import api, fields, models, _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

# Fixed external ids, see data/inspection_activity_data.xml.
INSPECTION_ACTIVITY_XMLID = 'inspection.mail_act_equipment_inspection'
WAITING_ACTIVITY_XMLID = 'inspection.mail_act_equipment_inspection_waiting'
# States in which the inspection still needs an equipment todo.
ACTIVE_STATES = ('draft', 'ready', 'waiting')
# States in which the todo must be closed.
TERMINAL_STATES = ('success', 'failed', 'canceled')


class MailActivity(models.Model):
    """Add the minimal inspection link and guard manual completion."""

    _inherit = 'mail.activity'

    inspection_id = fields.Many2one(
        'qc.inspection', string='设备检查', index='btree_not_null',
        ondelete='cascade', copy=False,
        help='由设备检查自动生成的活动所对应的检查单；普通活动为空。')
    _inspection_activity_unique = models.UniqueIndex(
        '(inspection_id) WHERE inspection_id IS NOT NULL',
        '同一检查只能有一个待办活动。',
    )

    # ------------------------------------------------------------------
    # Completion guard
    # ------------------------------------------------------------------
    def _inspection_completion_blocked(self):
        return self.filtered(
            lambda activity: activity.inspection_id
            and activity.inspection_id.state in ACTIVE_STATES)

    def _check_inspection_completion(self):
        """Refuse to complete a todo while its inspection is still open.

        The user is pointed to the inspection workflow instead: the activity is
        closed by the sync logic when the inspection really ends.
        """
        blocked = self._inspection_completion_blocked()
        if not blocked:
            return
        inspection = blocked[0].inspection_id
        if inspection.state == 'waiting' and inspection.inspection_supervisor_id:
            hint = _('该检查结果为异常，需要检查主管在检查单中确认异常或改为合格。')
        elif inspection.state == 'waiting':
            hint = _('该检查没有配置检查主管，请先在设备“检查”页签补齐检查主管，再由主管确认。')
        else:
            hint = _('请先在检查单中完成“开始检查 → 提交检查”的流程。')
        raise UserError(_(
            '“%(activity)s”是设备检查自动生成的待办，不能直接在活动上完成。\n'
            '%(hint)s\n'
            '请打开检查单 %(inspection)s 处理；检查结束后该活动会自动关闭。',
            activity=blocked[0].summary or inspection.name,
            hint=hint,
            inspection=inspection.name,
        ))

    def _action_done(self, feedback=False, attachment_ids=None):
        # All native completion entry points end up here: action_done,
        # action_feedback, action_feedback_schedule_next and
        # mail.activity.mixin.activity_feedback.  One interception point keeps
        # the rule consistent for the chatter, the kanban and every module.
        # The guard has deliberately no ``context`` escape hatch: the RPC
        # context is client supplied and therefore forgeable.  The sync closes
        # activities with ``unlink()`` (see ``_inspection_unlink``), never with
        # a completion path, so no internal caller needs to bypass this.
        self._check_inspection_completion()
        return super()._action_done(feedback=feedback, attachment_ids=attachment_ids)

    def _inspection_unlink(self):
        """Close inspection activities from the sync itself (no feedback)."""
        return self.sudo().unlink()

    # ------------------------------------------------------------------
    # Open the inspection (not only its equipment) from the activity
    # ------------------------------------------------------------------
    def action_open_document(self):
        self.ensure_one()
        inspection = self.inspection_id
        if inspection and inspection.has_access('read'):
            action = self.env['ir.actions.actions']._for_xml_id('inspection.action_inspections')
            view = self.env.ref('inspection.view_inspection_form', raise_if_not_found=False)
            action.update(res_id=inspection.id)
            if view:
                action['views'] = [(view.id, 'form')]
            return action
        # No access to the inspection: keep the native behaviour (equipment or
        # the activity form).  Never sudo here, the access rules must apply.
        return super().action_open_document()


class QcInspection(models.Model):
    """Keep the single equipment activity in sync with the inspection."""

    _inherit = 'qc.inspection'

    # ------------------------------------------------------------------
    # Who confirms an abnormal inspection?
    # ------------------------------------------------------------------
    def _inspection_activity_type(self):
        self.ensure_one()
        xmlid = WAITING_ACTIVITY_XMLID if self.state == 'waiting' else INSPECTION_ACTIVITY_XMLID
        return self.env.ref(xmlid)

    def _inspection_confirmation_target(self):
        """Explicit supervisor of this inspection, never an inferred one.

        The inspection stores the supervisor snapshot copied from the equipment
        at creation time (``inspection_supervisor_id``).  Changing the equipment
        supervisor afterwards does not silently reassign existing tasks.  When
        a legacy record has no snapshot, the todo stays on the inspector (the
        record responsible) with a clear "configuration missing" note; we never
        guess a manager from the category, the owner or the manager group.

        :return: ``(user, missing_supervisor_hint)``
        """
        self.ensure_one()
        supervisor = self.inspection_supervisor_id
        if supervisor:
            return supervisor, False
        return (self.user or self.equipment_id.technician_user_id or self.env.user), True

    # ------------------------------------------------------------------
    # Activity values
    # ------------------------------------------------------------------
    def _inspection_action_url(self):
        """Stable Odoo 19 web URL to open this inspection from an activity."""
        self.ensure_one()
        action = self.env.ref('inspection.action_inspections', raise_if_not_found=False)
        if not action:
            return False
        return f'/odoo/action-{action.id}/{self.id}'

    def _inspection_activity_vals(self):
        """Values of the activity that represents this inspection."""
        self.ensure_one()
        equipment = self.equipment_id
        deadline = self.inspection_date or fields.Date.context_today(self)
        item = self.inspection_test_name or self.name
        if self.state == 'waiting':
            user, needs_supervisor = self._inspection_confirmation_target()
            summary = _('待确认异常：%(equipment)s｜%(item)s',
                        equipment=equipment.name, item=item)
            if needs_supervisor:
                hint = _('该检查没有配置检查主管，本次提醒暂保留给检查员；'
                         '请先在设备“检查”页签补齐检查主管，再由主管在检查单中确认异常或改为合格。')
            else:
                hint = _('检查结果为异常，需要检查主管在检查单中确认异常或改为合格。')
        else:
            user = self.user or equipment.technician_user_id or self.env.user
            summary = _('设备检查：%(equipment)s｜%(item)s',
                        equipment=equipment.name, item=item)
            hint = _('请打开检查单填写并提交检查结果；检查结束前该活动不能直接完成，检查结束后会自动关闭。')
        details = [
            _('检查编号：%(name)s', name=self.name),
            _('检查方案：%(test)s', test=item),
            _('设备：%(equipment)s', equipment=equipment.display_name),
            _('业务日期：%(date)s', date=fields.Date.to_string(deadline)),
        ]
        note = Markup('<p>%s</p>') % Markup('<br/>').join(escape(line) for line in details)
        note += Markup('<p>%s</p>') % escape(hint)
        url = self._inspection_action_url()
        if url:
            note += Markup('<p><a href="%s">%s</a></p>') % (escape(url), escape(_('打开检查单')))
        return {
            'activity_type_id': self._inspection_activity_type().id,
            'summary': summary,
            'note': note,
            'date_deadline': deadline,
            'user_id': user.id,
            'automated': True,
            'inspection_id': self.id,
            'res_model_id': self.env['ir.model']._get_id('maintenance.equipment'),
            'res_id': equipment.id,
        }

    # ------------------------------------------------------------------
    # Synchronisation
    # ------------------------------------------------------------------
    def _sync_inspection_activity(self):
        """Ensure one activity per inspection and keep its fields in sync.

        The search and the update are done in sudo so that a supervisor
        transition performed by the inspector can find the activity that was
        just handed over to the supervisor; every write is a system side effect
        of a lifecycle change the current user was allowed to make.  Creation
        stays under the current user's own rights (see the comment below).
        """
        Activity = self.env['mail.activity']
        for inspection in self:
            equipment = inspection.equipment_id
            existing = Activity.sudo().search([('inspection_id', '=', inspection.id)])
            if not equipment or inspection.state in TERMINAL_STATES:
                if existing:
                    existing._inspection_unlink()
                continue
            if inspection.state not in ACTIVE_STATES:
                # ``plan`` (and any future calendar-only entry) is not a real
                # inspection: a future plan must never create a todo.
                continue
            values = inspection._inspection_activity_vals()
            if existing:
                activity = existing[0]
                if len(existing) > 1:
                    existing[1:]._inspection_unlink()
                changed = (
                    activity.activity_type_id.id != values['activity_type_id']
                    or (activity.summary or '') != values['summary']
                    or activity.user_id.id != values['user_id']
                    or activity.date_deadline != values['date_deadline']
                    or activity.res_id != values['res_id']
                    or activity.res_model_id.id != values['res_model_id']
                    or not activity.automated
                )
                if changed:
                    activity.sudo().write({
                        key: value for key, value in values.items()
                        if key not in ('inspection_id', 'res_model_id', 'res_id')
                    })
            elif values['user_id'] == self.env.uid:
                # Normal generation: create with the current user's own rights.
                Activity.create(values)
            else:
                # Lifecycle hand-over to another responsible (for instance the
                # inspector confirming an abnormal check creates the supervisor
                # todo).  The native create would then require equipment write
                # access for the current user, which an inspector deliberately
                # does not have.  Check the caller's access explicitly instead
                # of hiding a record: the equipment and the inspection must be
                # readable by the current user, and creation is a system side
                # effect of the transition they were allowed to make.
                equipment.check_access('read')
                inspection.check_access('read')
                Activity.sudo().create(values)

    # ------------------------------------------------------------------
    # Lifecycle hooks
    # ------------------------------------------------------------------
    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        # Manual, batch and cycle generation all converge on create(), so this
        # single hook covers every entry point.
        records._sync_inspection_activity()
        return records

    def write(self, vals):
        result = super().write(vals)
        if {'state', 'user'} & set(vals):
            # State machine and permission checks already ran in super().
            self._sync_inspection_activity()
        return result

    def unlink(self):
        # Draft deletion must not leave a dangling activity behind.  The FK is
        # also ON DELETE CASCADE; removing the rows explicitly keeps the bus
        # activity counters of the assigned users correct.
        activities = self.env['mail.activity'].sudo().search([('inspection_id', 'in', self.ids)])
        activities._inspection_unlink()
        return super().unlink()

    # ------------------------------------------------------------------
    # Backfill
    # ------------------------------------------------------------------
    @api.model
    def _sync_inspection_activities(self):
        """Idempotent backfill for open inspections that have no todo yet.

        Only real, still open equipment inspections (draft / ready / waiting)
        are processed.  Completed or canceled inspections are never given a new
        activity, existing activities are updated instead of duplicated, so the
        method can safely be called from post_init or a migration script and
        repeated any number of times.
        """
        inspections = self.sudo().search([
            ('equipment_id', '!=', False),
            ('state', 'in', list(ACTIVE_STATES)),
        ])
        missing_supervisor = inspections.filtered(
            lambda record: not record.inspection_supervisor_id)
        if missing_supervisor:
            # Legacy records only: new inspections always snapshot a supervisor.
            # They keep the todo on their inspector (with a "configuration
            # missing" note) instead of guessing a manager.
            _logger.warning(
                'Inspection activity backfill: %s open inspection(s) have no supervisor '
                'snapshot and keep the todo on their inspector: %s',
                len(missing_supervisor), missing_supervisor.ids)
        inspections._sync_inspection_activity()
        return len(inspections)
