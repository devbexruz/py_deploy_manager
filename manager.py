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
NGINX_SITES_AVAILABLE = os.getenv("NGINX_SITES_AVAILABLE", "/etc/nginx/sites-available")
NGINX_SITES_ENABLED = os.getenv("NGINX_SITES_ENABLED", "/etc/nginx/sites-enabled")
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

    domain_configs = {}
    seen_domains = set()
    loaded_count = 0

    print("[*] Loyihalarni qayta yuklash boshlandi...")
    
    for proj in config.get("projects", []):
        print(proj)
        name = proj.get("name", "unnamed")
        package_name = proj.get("package_name", name)
        domain = proj.get("domain")
        workdir = proj.get("workdir", f"/home/ubuntu/py_deploy_manager/projects/{name}")
        module_name = proj.get("module", "main").replace(".", "/")
        backend_prefix = proj.get("backend_prefix", "")
        frontend_path = proj.get("frontend_path", "")
        app_var_name = proj.get("app", "app")
        
        if not domain:
            print(f"[!] {name} uchun domain ko'rsatilmagan. O'tkazib yuborildi.")
            continue
            
        if domain in seen_domains:
            print(f"[!] {domain} domeni bir necha marta ishlatilgan. {name} o'tkazib yuborildi.")
            continue
            
        seen_domains.add(domain)
        
        try:
            if workdir not in sys.path:
                sys.path.insert(0, workdir)
                
            module_path = os.path.join(workdir, f"{module_name}.py")
            
            # 1. VIRTUAL PACKAGE YARATISH: 
            # Python loyiha papkasini rasmiy package deb o'ylashi va 
            # relative (.) importlarni to'g'ri topishi uchun uni sys.modules ga qo'shamiz
            if package_name not in sys.modules:
                pkg_spec = importlib.machinery.ModuleSpec(package_name, None, is_package=True)
                pkg_module = importlib.util.module_from_spec(pkg_spec)
                pkg_module.__path__ = [workdir] # Package ichini aynan workdir ga bog'laymiz
                pkg_module.__file__ = os.path.join(workdir, "__init__.py")
                sys.modules[package_name] = pkg_module

            # Modul nomini oddiy "main" emas, "package_name.main" qilib yuklaymiz
            full_module_key = f"{package_name}.{module_name}"
            
            # KESH MUAMMOSINI YECHISH
            if full_module_key in sys.modules:
                del sys.modules[full_module_key]
            
            spec = importlib.util.spec_from_file_location(full_module_key, module_path)
            module = importlib.util.module_from_spec(spec)
            
            # ENG MUHIM QADAM: Modul qaysi paketga tegishli ekanligini bildirish
            module.__package__ = package_name
            
            # Modulni tizimga qo'shamiz va ishga tushiramiz
            sys.modules[full_module_key] = module
            spec.loader.exec_module(module)
            
            sub_app = getattr(module, app_var_name)
            
            manager_app.mount(f"/{package_name}", sub_app)
            print(f"[+] Loaded Python App: {name} (URL prefix: /{package_name}) from {workdir}")
            
            # ... (frontend_location va Nginx shablonlarini to'ldirish qismi o'zgarishsiz qoladi)
            if frontend_path:
                frontend_location = f"location / {{ root {frontend_path}; index index.html; try_files $uri $uri/ /index.html; }}"
            else:
                frontend_location = ""
                
            rendered_template = nginx_template.replace(
                "{package_name}", package_name
            ).replace(
                "{domain}", domain
            ).replace(
                "{frontend_location}", frontend_location
            ).replace(
                "{backend_prefix}", f"{backend_prefix}/" if backend_prefix else ""
            )
            
            domain_configs[domain] = rendered_template
            loaded_count += 1
            
        except Exception as e:
            print(f"[!] {name} yuklanishda xato berdi: {e}")

    if loaded_count > 0:
        update_nginx_multiple(domain_configs)
    else:
        print("[!] Hech bitta loyiha yuklanmadi. Nginx o'zgartirilmadi.")

def check_config_file(path):
    if not os.path.exists(path):
        return True
    print(f"[!] Configuration fayl allaqachon mavjud: {path}")
    return False


def update_nginx_multiple(domain_configs: dict):
    try:
        updated_any = False
        for domain, config_content in domain_configs.items():
            conf_path = os.path.join(NGINX_SITES_AVAILABLE, f"{domain}.conf")
            symlink_path = os.path.join(NGINX_SITES_ENABLED, f"{domain}.conf")
            if not check_config_file(symlink_path):
                continue
            temp_conf_path = f"{conf_path}.tmp"
            
            with open(temp_conf_path, "w") as f:
                print("symlink_path:", symlink_path)
                f.write(config_content)
            os.replace(temp_conf_path, conf_path)
            os.symlink(conf_path, symlink_path)
            updated_any = True
            
        if updated_any:
            # Sintaksis tekshiruvi
            result = subprocess.run(["sudo", "nginx", "-t"], capture_output=True, text=True)
            if result.returncode != 0:
                raise Exception(f"Nginx sintaksisida xato: {result.stderr}")
                
            # Reload
            subprocess.run(["sudo", "systemctl", "reload", "nginx"], check=True)
            print("[+] Nginx konfiguratsiyalari muvaffaqiyatli yangilandi va reload qilindi.")
            
    except Exception as e:
        print(f"[!] Nginx-ni yangilashda jiddiy xatolik: {e}")