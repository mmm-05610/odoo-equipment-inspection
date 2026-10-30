FROM odoo@sha256:27f920e002c43a1af7022a68f3cc17db71a2f9f3348800cb050721ce54893e49
COPY --chown=odoo:odoo addons/inspection /mnt/extra-addons/inspection
COPY --chown=odoo:odoo vendor/quality_control_oca /mnt/extra-addons/quality_control_oca
COPY scripts/load_factory_demo.py /opt/inspection/load_factory_demo.py
COPY deploy/seed_public.py /opt/inspection/seed_public.py
