# License AGPL-3.0-or-later.
"""Installation wiring. Demo history uses the same workflow as the UI."""


def post_init_hook(env):
    sync_runtime(env)
    prepare_demo_history(env)


def sync_runtime(env):
    env['qc.inspection']._sync_inspection_activities()


def prepare_demo_history(env):
    # No fixtures means a normal installation: do not create demonstration data.
    if env.ref('inspection.factory_equipment_p01', raise_if_not_found=False):
        from .demo_seed import seed_factory_history
        seed_factory_history(env)
