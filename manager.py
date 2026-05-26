import yaml
import importlib
from fastapi import FastAPI

# Markaziy yagona FastAPI protsessi
manager_app = FastAPI(title="PyDeployManager")

def bootstrap():
    try:
        with open("projects.yaml", "r") as file:
            config = yaml.safe_load(file)
            
        for proj in config.get("projects", []):
            # Loyihani dinamik import qilish
            module = importlib.import_module(proj["module"])
            sub_app = getattr(module, proj["app"])
            
            # Loyiha nomi (domeni) bilan mount qilish
            manager_app.mount(f"/{proj['name']}", sub_app)
            print(f"[+] Loaded: {proj['name']}")
            
    except Exception as e:
        print(f"[!] Bootstrapping xatolik: {e}")

# Ishga tushganda loyihalarni yuklaymiz
bootstrap()
