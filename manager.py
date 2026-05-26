import os
import sys
import yaml
import importlib
import importlib.util
import subprocess
from fastapi import FastAPI
from contextlib import asynccontextmanager
from dotenv import load_dotenv

load_dotenv()

PROJECTS_YAML_PATH = os.getenv("PROJECTS_YAML_PATH", "projects.yaml")
NGINX_CONF_PATH = os.getenv("NGINX_CONF_PATH", "/etc/nginx/sites-available/py_deploy_manager.conf")
NGINX_SYMLINK = os.getenv("NGINX_SYMLINK", "/etc/nginx/sites-enabled/py_deploy_manager.conf")
NGINX_TEMPLATE_PATH = os.getenv("NGINX_TEMPLATE_PATH", "nginx_template.conf")

@asynccontextmanager
async def lifespan(app: FastAPI):
    reload_all_projects_and_nginx()
    yield

manager_app = FastAPI(title="PyDeployManager", lifespan=lifespan)

def reload_all_projects_and_nginx():
    if not os.path.exists(PROJECTS_YAML_PATH):
        print(f"[!] {PROJECTS_YAML_PATH} topilmadi. Bo'sh rejimda ishga tushdi.")
        return

    with open(PROJECTS_YAML_PATH, "r") as file:
        config = yaml.safe_load(file)

    if not os.path.exists(NGINX_TEMPLATE_PATH):
        print(f"[!] {NGINX_TEMPLATE_PATH} topilmadi. Nginx o'zgartirilmadi.")
        return

    with open(NGINX_TEMPLATE_PATH, "r") as template_file:
        nginx_template = template_file.read()

    nginx_config_content = ""
    loaded_count = 0

    print("[*] Loyihalarni qayta yuklash boshlandi...")
    
    for proj in config.get("projects", []):
        name = proj["name"]
        domain = proj["domain"]
        workdir = proj.get("workdir", f"/home/ubuntu/py_deploy_manager/projects/{name}")
        app_str = proj["app"]
        
        try:
            module_name, app_var_name = app_str.split(":")
            
            if workdir not in sys.path:
                sys.path.insert(0, workdir)
                
            module_path = os.path.join(workdir, f"{module_name}.py")
            
            # KESH MUAMMOSINI YECHISH: Agar modul eski keshda bo'lsa, uni o'chiramiz
            full_module_key = f"{name}_{module_name}"
            if full_module_key in sys.modules:
                del sys.modules[full_module_key]
            
            spec = importlib.util.spec_from_file_location(full_module_key, module_path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            
            sub_app = getattr(module, app_var_name)
            
            manager_app.mount(f"/{name}", sub_app)
            print(f"[+] Loaded Python App: {name} (URL prefix: /{name}) from {workdir}")
            
            rendered_template = nginx_template.replace("{name}", name).replace("{domain}", domain).replace("{workdir}", workdir)
            nginx_config_content += rendered_template + "\n"
            loaded_count += 1
            
        except Exception as e:
            print(f"[!] {name} yuklanishda xato berdi: {e}")

    if loaded_count > 0:
        update_nginx(nginx_config_content)
    else:
        print("[!] Hech bitta loyiha yuklanmadi. Nginx o'zgartirilmadi.")

def update_nginx(config_content: str):
    # Ataylab vaqtinchalik (temp) faylga yozamiz, Nginx buzilib qolmasligi uchun
    temp_conf_path = f"{NGINX_CONF_PATH}.tmp"
    try:
        with open(temp_conf_path, "w") as f:
            f.write(config_content)
            
        # 1. Vaqtinchalik faylni tekshiramiz (Nginx -t faqat asosiy fayllarni ko'ra olgani uchun,
        # avval yozib olib, xato bo'lsa darhol orqaga qaytarish xavfsizroq)
        os.replace(temp_conf_path, NGINX_CONF_PATH)
        
        if not os.path.exists(NGINX_SYMLINK):
            os.symlink(NGINX_CONF_PATH, NGINX_SYMLINK)
            
        # 2. Sintaksis tekshiruvi
        result = subprocess.run(["sudo", "nginx", "-t"], capture_output=True, text=True)
        if result.returncode != 0:
            raise Exception(f"Nginx sintaksisida xato: {result.stderr}")
            
        # 3. Reload
        subprocess.run(["sudo", "systemctl", "reload", "nginx"], check=True)
        print("[+] Nginx konfiguratsiyasi muvaffaqiyatli yangilandi va reload qilindi.")
        
    except Exception as e:
        print(f"[!] Nginx-ni yangilashda jiddiy xatolik: {e}")
        # Xato bo'lsa, vaqtinchalik fayl qolib ketgan bo'lsa o'chiramiz
        if os.path.exists(temp_conf_path):
            os.remove(temp_conf_path)