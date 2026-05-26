import os
import sys
import yaml
import importlib
import importlib.util
import subprocess
from fastapi import FastAPI
from contextlib import asynccontextmanager
from dotenv import load_dotenv

# .env faylini yuklash
load_dotenv()

# Konfiguratsiyalarni .env dan o'qish yoki default qiymatlardan foydalanish
PROJECTS_YAML_PATH = os.getenv("PROJECTS_YAML_PATH", "projects.yaml")
NGINX_CONF_PATH = os.getenv("NGINX_CONF_PATH", "/etc/nginx/sites-available/py_deploy_manager.conf")
NGINX_SYMLINK = os.getenv("NGINX_SYMLINK", "/etc/nginx/sites-enabled/py_deploy_manager.conf")
NGINX_TEMPLATE_PATH = os.getenv("NGINX_TEMPLATE_PATH", "nginx_template.conf")

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Manager ishga tushganda (yoki restart bo'lganda) hamma narsani yangilash
    reload_all_projects_and_nginx()
    yield

# Markaziy FastAPI protsessi
manager_app = FastAPI(title="PyDeployManager", lifespan=lifespan)

def reload_all_projects_and_nginx():
    if not os.path.exists(PROJECTS_YAML_PATH):
        print(f"[!] {PROJECTS_YAML_PATH} topilmadi. Bo'sh rejimda ishga tushdi.")
        return

    with open(PROJECTS_YAML_PATH, "r") as file:
        config = yaml.safe_load(file)

    # Nginx shablonini fayldan o'qish
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
            # 1. Loyihani dinamik import qilish (workdir va fayl nomi orqali)
            module_name, app_var_name = app_str.split(":")
            
            # workdir ni vaqtincha sys.path ga qo'shamiz (importlar to'g'ri ishlashi uchun)
            if workdir not in sys.path:
                sys.path.insert(0, workdir)
                
            module_path = os.path.join(workdir, f"{module_name}.py")
            spec = importlib.util.spec_from_file_location(f"{name}_{module_name}", module_path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            
            sub_app = getattr(module, app_var_name)
            
            # 2. Yangitdan mount qilish
            manager_app.mount(f"/{name}", sub_app)
            print(f"[+] Loaded Python App: {name} (URL prefix: /{name}) from {workdir}")
            
            # 3. Nginx blokini generatsiya qilish
            rendered_template = nginx_template.replace("{name}", name).replace("{domain}", domain).replace("{workdir}", workdir)
            nginx_config_content += rendered_template + "\n"
            loaded_count += 1
            
        except Exception as e:
            print(f"[!] {name} yuklanishda xato berdi: {e}")

    # 4. Agar yuklangan loyihalar bo'lsa, Nginx-ni avto-sozlash va reload qilish
    if loaded_count > 0:
        update_nginx(nginx_config_content)
    else:
        print("[!] Hech bitta loyiha yuklanmadi. Nginx o'zgartirilmadi.")

def update_nginx(config_content: str):
    try:
        # Konfigga yozish
        with open(NGINX_CONF_PATH, "w") as f:
            f.write(config_content)
        
        # Symlink yaratish (agar yo'q bo'lsa)
        if not os.path.exists(NGINX_SYMLINK):
            os.symlink(NGINX_CONF_PATH, NGINX_SYMLINK)
            
        # Nginx test va reload (Zero Downtime)
        subprocess.run(["sudo", "nginx", "-t"], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run(["sudo", "systemctl", "reload", "nginx"], check=True)
        print("[+] Nginx konfiguratsiyasi muvaffaqiyatli yangilandi va reload qilindi.")
    except Exception as e:
        print(f"[!] Nginx-ni yangilashda xatolik: {e}")