"""Fixed demo filestore operations, executed in this project's data volume."""
import shutil,sys
from pathlib import Path
root=Path('/var/lib/odoo/filestore')
root.mkdir(parents=True,exist_ok=True)
def remove(name):
    path=root/name
    if path.exists():shutil.rmtree(path)
operation=sys.argv[1]
if operation=='prepare':
    remove('inspection_next')
    source=root/'inspection_template'
    if source.exists():shutil.copytree(source,root/'inspection_next')
    else:(root/'inspection_next').mkdir()
elif operation=='switch':
    remove('inspection_previous')
    if (root/'inspection_demo').exists():(root/'inspection_demo').rename(root/'inspection_previous')
    (root/'inspection_next').rename(root/'inspection_demo')
elif operation=='rollback':
    if (root/'inspection_previous').exists():
        remove('inspection_demo')
        (root/'inspection_previous').rename(root/'inspection_demo')
else:raise ValueError('Unsupported fixed operation')
