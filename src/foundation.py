import json
import shutil
import subprocess
import socket
from pathlib import Path
from enum import Enum
from typing import Annotated
import typer
from rich.console import Console
from rich.table import Table

APP_NAME = "foundation"
APP_DIR = typer.get_app_dir(APP_NAME)
PROXY_PATH = Path(APP_DIR) / "compose.json"
SERVICES_DIR = Path(APP_DIR) / "services"
SERVICES_PATH = SERVICES_DIR / "compose.json"

ctx_settings = {"help_option_names": ["-h", "--help"]}

app = typer.Typer(name=APP_NAME, help="CLI tool for managing Docker services with automatic reverse proxying and SSL termination.", context_settings=ctx_settings, no_args_is_help=True)
env_app = typer.Typer(help="Manage environment variables.", context_settings=ctx_settings, no_args_is_help=True)
volume_app = typer.Typer(help="Manage persistent storage volumes.", context_settings=ctx_settings, no_args_is_help=True)
domain_app = typer.Typer(help="Manage domains, SSL, and redirects.", context_settings=ctx_settings, no_args_is_help=True)

app.add_typer(env_app, name="env")
app.add_typer(volume_app, name="volume")
app.add_typer(domain_app, name="domain")

console = Console()

class Docker:
    @staticmethod
    def installed():
        try:
            subprocess.run(["docker", "--version"], capture_output=True, check=True)
            return True
        except Exception:
            return False
    
    @staticmethod
    def running():
        try:
            subprocess.run(["docker", "info"], capture_output=True, check=True)
            return True
        except Exception as error:
            return "permission denied" in error.stderr.decode().lower()
    
    @staticmethod
    def permissions():
        try:
            subprocess.run(["docker", "info"], capture_output=True, check=True)
            return True
        except Exception:
            return False

    @staticmethod
    def install():
        try:
            subprocess.run(["curl", "-fsSL", "https://get.docker.com", "-o", "get-docker.sh"], capture_output=True, check=True)
            subprocess.run(["sh", "get-docker.sh"], capture_output=True, check=True)
        finally:    
            Path("get-docker.sh").unlink(missing_ok=True)

    @staticmethod
    def is_image(source):
        try:
            subprocess.run(["docker", "manifest", "inspect", source], capture_output=True, check=True, timeout=20)
            return True
        except Exception:
            return False
        
    @staticmethod
    def get_compose(compose_path):
        subprocess.run(["docker", "compose", "--file", compose_path, "config", "--format", "json"], capture_output=True, check=True)
        return json.loads(compose_path.read_text())
    
    @staticmethod
    def get_compose_status(compose_path):
        compose = Docker.get_compose(compose_path)
        services = compose.get("services", {})
        result = subprocess.run(["docker", "compose", "--file", compose_path, "ps", "--all", "--format", "{{json .}}"], capture_output=True, text=True, check=True)
        services_status = [json.loads(line) for line in result.stdout.strip().split("\n") if line]
        services_status = {service_status.get("Service"): service_status for service_status in services_status}

        return {
            service_name: {
                "state": services_status.get(service_name, {}).get("State"),
                "status": services_status.get(service_name, {}).get("Status"),
                "created_at": services_status.get(service_name, {}).get("CreatedAt")
            } for service_name in services
        }
    
    @staticmethod
    def write_compose(compose_path, compose):
        compose_str = json.dumps(compose, indent=2)
        subprocess.run(["docker", "compose", "--file", "-", "config"], input=compose_str, capture_output=True, text=True, check=True)
        compose_path.parent.mkdir(parents=True, exist_ok=True)
        compose_path.write_text(compose_str, encoding="utf-8")

    @staticmethod
    def build(tag, service_dir):
        subprocess.run(["docker", "build", "--tag", tag, service_dir], capture_output=True, check=True)

    @staticmethod
    def build_from_railpack_plan(tag, service_dir, railpack_plan_path):
        if subprocess.run(["docker", "buildx", "inspect", "railpack-builder"], capture_output=True).returncode == 0:
            subprocess.run(["docker", "buildx", "use", "railpack-builder"], capture_output=True, check=True)
        else:
            subprocess.run(["docker", "buildx", "create", "--name", "railpack-builder", "--driver", "docker-container", "--use", "--bootstrap"], capture_output=True, check=True)

        subprocess.run([
            "docker", "buildx", "build",
            "--build-arg", "BUILDKIT_SYNTAX=ghcr.io/railwayapp/railpack-frontend",
            "--tag", tag, "--file", railpack_plan_path, service_dir, "--load"
        ], capture_output=True, check=True)

    @staticmethod
    def compose_build(compose_path, service_name=None):
        subprocess.run(list(filter(None, ["docker", "compose", "-f", compose_path, "build", service_name])), capture_output=True, check=True)
    
    @staticmethod
    def compose_pull(compose_path, service_name=None):
        subprocess.run(list(filter(None, ["docker", "compose", "-f", compose_path, "pull", service_name])), capture_output=True, check=True)

    @staticmethod
    def compose_up(compose_path, service_name=None):
        subprocess.run(list(filter(None, ["docker", "compose", "-f", compose_path, "up", service_name, "--detach", "--remove-orphans"])), capture_output=True, check=True)

    @staticmethod
    def compose_down(compose_path):
        subprocess.run(list(filter(None, ["docker", "compose", "-f", compose_path, "down", "--remove-orphans"])), capture_output=True, check=True)

class Git:
    @staticmethod
    def installed():
        try:
            subprocess.run(["git", "--version"], capture_output=True, check=True)
            return True
        except Exception:
            return False
    
    @staticmethod
    def install():
        try:
            subprocess.run(["curl", "-fsSL", "https://raw.githubusercontent.com/ElliottStorey/git-install/main/install.sh", "-o", "get-git.sh"], capture_output=True, check=True)
            subprocess.run(["sh", "get-git.sh"], capture_output=True, check=True)
        finally:    
            Path("get-git.sh").unlink(missing_ok=True)

    @staticmethod
    def is_url(source):
        return source.startswith(("http://", "https://", "git@", "ssh://"))

    @staticmethod
    def is_repo(source):
        try:
            subprocess.run(["git", "ls-remote", source], capture_output=True, check=True, timeout=10)
            return True
        except Exception:
            return False

    @staticmethod
    def clone(source, service_dir):
        service_dir.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "clone", source, "."], cwd=service_dir, capture_output=True, check=True)

    @staticmethod
    def reset(service_dir):
        subprocess.run(["git", "fetch"], cwd=service_dir, capture_output=True, check=True)
        result = subprocess.run(["git", "rev-list", "--count", "HEAD..@{u}"], cwd=service_dir, capture_output=True, text=True, check=True)
        if int(result.stdout.strip()) > 0:
            subprocess.run(["git", "reset", "--hard", "@{u}"], cwd=service_dir)

class Railpack:
    @staticmethod
    def installed():
        try:
            subprocess.run(["railpack", "--version"], capture_output=True, check=True)
            return True
        except Exception:
            return False

    @staticmethod
    def install():
        try:
            subprocess.run(["curl", "-fsSL", "https://railpack.com/install.sh", "-o", "get-railpack.sh"], capture_output=True, check=True)
            subprocess.run(["sh", "get-railpack.sh"], capture_output=True, check=True)
        finally:    
            Path("get-railpack.sh").unlink(missing_ok=True)
    
    @staticmethod
    def prepare(service_dir, plan_out):
        subprocess.run(["railpack", "prepare", service_dir, "--plan-out", plan_out], capture_output=True, check=True)

class Output:
    @staticmethod
    def info(message, next_message=None, next_command=None, exit=False):
        console.print(f"{message}.")
        if next_command and next_message:
            console.print(f"Try running [bold cyan]{APP_NAME} {next_command}[/] to {next_message}.")
        elif next_message:
            console.print(f"Try to {next_message}.")
        if exit: raise typer.Exit()

    @staticmethod
    def success(message, next_message=None, next_command=None, exit=False):
        console.print(f"[bold green]Success:[/] {message}!")
        if next_command and next_message:
            console.print(f"Try running [bold cyan]{APP_NAME} {next_command}[/] to {next_message}.")
        elif next_message:
            console.print(f"Try to {next_message}.")
        if exit: raise typer.Exit()

    @staticmethod
    def error(message, next_message=None, next_command=None, exception=None, exit=True):
        console.quiet = False
        if isinstance(exception, subprocess.CalledProcessError):
            stderr, stdout = exception.stderr, exception.stdout
            if stderr: console.print(f"[red]{stderr.decode().strip() if isinstance(stderr, bytes) else stderr.strip()}[/]")
            elif stdout: console.print(f"[red]{stdout.decode().strip() if isinstance(stdout, bytes) else stdout.strip()}[/]")
        elif exception:
            console.print_exception(show_locals=True)

        console.print(f"[bold red]Error:[/] {message}.")
        if next_command and next_message:
            console.print(f"Try running [bold cyan]{APP_NAME} {next_command}[/] to {next_message}.")
        elif next_message:
            console.print(f"Try to {next_message}.")
        if exit: raise typer.Exit(code=1)

class RestartPolicy(str, Enum):
    no = "no"
    always = "always"
    on_failure = "on-failure"
    unless_stopped = "unless-stopped"

def detect_gpu_environment():
    if shutil.which("nvidia-smi"): return "nvidia"
    if Path("/dev/kfd").exists() and Path("/dev/dri").exists(): return "amd"
    if Path("/dev/dri").exists(): return "intel"
    return None

@app.callback()
def main(ctx: typer.Context):
    if ctx.invoked_subcommand in [None, "init"]: return

    if not Docker.installed(): Output.error("Docker is not installed", "install all dependencies", "init")
    if not Docker.running(): Output.error("Docker is not running", "start Docker")
    if not Docker.permissions(): Output.error("Docker permission denied", "re-run with sudo")
    if not Git.installed(): Output.error("Git is not installed", "install all dependencies", "init")
    if not Railpack.installed(): Output.error("Railpack is not installed", "install all dependencies", "init")
    if not PROXY_PATH.is_file() or not SERVICES_PATH.is_file(): Output.error("Foundation is not initialised", "setup the environment", "init")

    try:
        Docker.get_compose(PROXY_PATH)
        Docker.get_compose(SERVICES_PATH)
    except Exception:
        Output.error("Configuration files are corrupted", "restore the environment", "init")

    if ctx.invoked_subcommand == "deploy": return

    try:
        proxy_status = Docker.get_compose_status(PROXY_PATH)
        if not all(s["state"] == "running" for s in proxy_status.values()): raise
    except Exception:
        Output.error("Reverse proxy is not running", "restart it", "deploy")

# --- CORE SYSTEM ---

@app.command(help="Install dependencies and start the proxy.")
def init(default_email: Annotated[str, typer.Option(help="Default email address used for Let's Encrypt SSL.", prompt="Default Let's Encrypt email")]):
    if not Docker.installed():
        with console.status("Installing Docker..."):
            try: Docker.install(); Output.success("Docker installed!")
            except Exception as e: Output.error("Could not install Docker", exception=e)

    if not Git.installed():
        with console.status("Installing Git..."):
            try: Git.install(); Output.success("Git installed!")
            except Exception as e: Output.error("Could not install Git", exception=e)
    
    if not Railpack.installed():
        with console.status("Installing Railpack..."):
            try: Railpack.install(); Output.success("Railpack installed!")
            except Exception as e: Output.error("Could not install Railpack", exception=e)
 
    try:
        services_compose = Docker.get_compose(SERVICES_PATH)
        services = services_compose.get("services", {})
        volumes = services_compose.get("volumes", {})
        Output.info("Using existing configuration files")
    except Exception:
        services, volumes = {}, {}

    proxy_compose = {
        "name": "foundation-proxy",
        "services": {
            "nginx-proxy": {
                "container_name": "nginx-proxy",
                "image": "nginxproxy/nginx-proxy",
                "volumes": ["certs:/etc/nginx/certs", "html:/usr/share/nginx/html", "/var/run/docker.sock:/tmp/docker.sock:ro"],
                "ports": ["80:80", "443:443"],
                "networks": ["foundation_network"],
                "restart": "unless-stopped"
            },
            "nginx-proxy-acme": {
                "container_name": "nginx-proxy-acme",
                "image": "nginxproxy/acme-companion",
                "environment": {"DEFAULT_EMAIL": default_email},
                "volumes": ["/var/run/docker.sock:/var/run/docker.sock:ro", "acme:/etc/acme.sh"],
                "volumes_from": ["nginx-proxy"],
                "networks": ["foundation_network"],
                "restart": "unless-stopped"
            }
        },
        "volumes": {"certs": {}, "html": {}, "acme": {}},
        "networks": {"foundation_network": {"name": "foundation_network"}}
    }

    services_compose = {
        "name": "foundation-services",
        "services": services, "volumes": volumes,
        "networks": {"foundation_network": {"external": True, "name": "foundation_network"}}
    }

    with console.status("Updating configuration files..."):
        try:
            Docker.write_compose(PROXY_PATH, proxy_compose)
            Docker.write_compose(SERVICES_PATH, services_compose)
            Output.success("Updated configuration files")
        except Exception as e: Output.error("Could not update configuration files", exception=e)

    with console.status("Deploying changes..."):
        try: deploy(report_success=False); Output.success("Foundation initialised", "create your first service", "create")
        except Exception: pass

@app.command(help="Create a new service interactively.")
def create(
    name: Annotated[str, typer.Argument(help="Name of the service to create.")],
    source: Annotated[str, typer.Option("--source", help="Git repository or Docker image.", prompt="Git repository or Docker image")]
):
    services_compose = Docker.get_compose(SERVICES_PATH)
    if name in services_compose.get("services", {}):
        Output.error(f"Service [bold italic]{name}[/] already exists", "delete it first", f"delete {name}")

    service_dir = SERVICES_DIR / name
    if Git.is_url(source):
        with console.status("Cloning repository..."):
            try: shutil.rmtree(service_dir, ignore_errors=True); Git.clone(source, service_dir); Output.success("Repository cloned")
            except Exception as e: Output.error("Could not clone repository", exception=e)

    service_compose = {
        "container_name": name,
        **({"build": str(service_dir)} if (service_dir / "Dockerfile").is_file() else {"image": f"foundation/{name}" if service_dir.is_dir() else source}),
        "networks": ["foundation_network"],
        "restart": "unless-stopped"
    }

    services_compose.setdefault("services", {})[name] = service_compose
    with console.status("Updating configuration files..."):
        try: Docker.write_compose(SERVICES_PATH, services_compose); Output.success("Updated configuration files")
        except Exception as e: Output.error("Could not update configuration files", exception=e)

    try: deploy(name, report_success=False); Output.success(f"Service [bold italic]{name}[/] created", "view its status", f"status {name}")
    except Exception: pass

@app.command(help="Update container resources and hardware allocation.")
def update(
    name: Annotated[str, typer.Argument(help="Name of the service.")],
    restart_policy: Annotated[RestartPolicy, typer.Option("--restart", help="Restart policy")] = None,
    cpus: Annotated[str, typer.Option("--cpus", help="Floating limit on CPUs (e.g., '1.5')")] = None,
    cpuset: Annotated[str, typer.Option("--cpuset", help="Pin to specific CPU cores (e.g., '0,3')")] = None,
    memory: Annotated[str, typer.Option("--memory", help="Hard limit on memory (e.g., '2G')")] = None,
    gpu: Annotated[bool, typer.Option("--gpu/--no-gpu", help="Auto-detect and enable/disable GPU")] = None,
    gpu_devices: Annotated[str, typer.Option("--gpu-devices", help="Specific GPUs to use (e.g., 'all', '1', 'GPU-xyz')")] = "all"
):
    services_compose = Docker.get_compose(SERVICES_PATH)
    service = services_compose.get("services", {}).get(name)
    if not service: Output.error(f"Service [bold italic]{name}[/] not found")

    if restart_policy: service["restart"] = restart_policy.value
    if cpuset: service["cpuset"] = cpuset

    deploy_block = service.setdefault("deploy", {})
    resources = deploy_block.setdefault("resources", {})
    
    if cpus or memory:
        limits = resources.setdefault("limits", {})
        if cpus: limits["cpus"] = cpus
        if memory: limits["memory"] = memory

    if gpu is not None:
        resources.pop("reservations", None)
        service["devices"] = [d for d in service.get("devices", []) if not d.startswith(("/dev/dri", "/dev/kfd"))]
        if not service["devices"]: service.pop("devices", None)

        if gpu:
            vendor = detect_gpu_environment()
            if vendor == "nvidia":
                res = resources.setdefault("reservations", {})
                dev_cfg = {"driver": "nvidia", "capabilities": ["gpu"]}
                if gpu_devices.startswith("GPU-"): dev_cfg["device_ids"] = [gpu_devices]
                elif "," in gpu_devices: dev_cfg["device_ids"] = gpu_devices.split(",")
                else: dev_cfg["count"] = gpu_devices
                res["devices"] = [dev_cfg]
            elif vendor in ["amd", "intel"]:
                nodes = [""] if gpu_devices == "all" else gpu_devices.split(",")
                for node in nodes: service.setdefault("devices", []).append(f"/dev/dri{'/renderD'+node if node else ''}:/dev/dri{'/renderD'+node if node else ''}")
                if vendor == "amd": service.setdefault("devices", []).append("/dev/kfd:/dev/kfd")
            else: Output.error("Could not auto-detect a supported GPU on this host.")

    if not resources: deploy_block.pop("resources", None)
    if not deploy_block: service.pop("deploy", None)

    with console.status("Applying updates..."):
        Docker.write_compose(SERVICES_PATH, services_compose)
    deploy(name)

@app.command(help="Permanently remove a service and its configuration.")
def delete(name: Annotated[str, typer.Argument(help="Name of the service to delete.")]):
    services_compose = Docker.get_compose(SERVICES_PATH)
    if name not in services_compose.get("services", {}): Output.success(f"Service [bold italic]{name}[/] not defined", exit=True)
    services_compose["services"].pop(name, None)
    with console.status("Cleaning up..."):
        Docker.write_compose(SERVICES_PATH, services_compose)
        shutil.rmtree(SERVICES_DIR / name, ignore_errors=True)
    try: deploy(name, report_success=False); Output.success(f"Service [bold italic]{name}[/] deleted", "view remaining", "status")
    except Exception: pass

@app.command(help="View global dashboard or inspect a specific service.")
def status(name: Annotated[str, typer.Argument(help="Specific service to inspect.")] = None):
    services_compose = Docker.get_compose(SERVICES_PATH)
    services = services_compose.get("services", {})
    services_status = Docker.get_compose_status(SERVICES_PATH)

    if not services: Output.info("No services defined", "add a service", "create", exit=True)

    if name:
        if name not in services: Output.error(f"Service [bold italic]{name}[/] not found")
        svc = services[name]
        state = services_status.get(name, {}).get("state", "-")
        color = "green" if state == "running" else "red"
        
        console.print(f"\n[bold {color}]■ {name}[/]")
        console.print(f"  [dim]Status:[/] [{color}]{state}[/] ({services_status.get(name, {}).get('status', '-')})")
        
        if "VIRTUAL_HOST" in svc.get("environment", {}):
            console.print(f"  [dim]Domains:[/] {svc['environment']['VIRTUAL_HOST']}")
            
        envs = {k:v for k,v in svc.get("environment", {}).items() if k not in ["VIRTUAL_HOST", "LETSENCRYPT_HOST", "LETSENCRYPT_EMAIL", "VIRTUAL_PORT"]}
        if envs:
            console.print("  [dim]Environment:[/]")
            for k, v in envs.items(): console.print(f"    - {k}={v}")
            
        if svc.get("volumes"):
            console.print("  [dim]Volumes:[/]")
            for v in svc["volumes"]: console.print(f"    - {v}")
            
        if "deploy" in svc or "devices" in svc:
            console.print("  [dim]Hardware Resources Allocated[/]")
        console.print("")
        return

    table = Table(title="Global Dashboard")
    table.add_column("Name", style="bold italic")
    table.add_column("Status")
    table.add_column("Uptime", style="dim")
    table.add_column("Domain")

    for s_name, s_status in services_status.items():
        state = s_status.get("state", "-")
        state = f"[green]{state}[/]" if state == "running" else f"[red]{state}[/]"
        host = services.get(s_name, {}).get("environment", {}).get("VIRTUAL_HOST")
        if host and s_name.startswith("redirect-"): host = f"[dim]{host} ➔ Redirect[/]"
        elif host: host = f"[link=https://{host.split(',')[0]}]{host.split(',')[0]}[/link]"
        table.add_row(s_name, state, s_status.get("status", "-"), host or "-")

    console.print(table)

@app.command(help="Build and start services. Pulls latest code/image.")
def deploy(name: Annotated[str, typer.Argument(help="Name of the service to deploy.")] = None, report_success: bool = True):
    services_compose = Docker.get_compose(SERVICES_PATH)
    services = services_compose.get("services", {})

    for s_name, service in services.items():
        if name and s_name != name: continue
        s_dir, build, image = SERVICES_DIR / s_name, service.get("build"), service.get("image", "")

        if build or image == f"foundation/{s_name}":
            with console.status(f"Updating repository for [bold italic]{s_name}[/]..."):
                try: Git.reset(s_dir)
                except Exception as e: Output.error(f"Could not update repository for [bold italic]{s_name}[/]", exception=e)

        if build:
            with console.status(f"Building [bold italic]{s_name}[/]..."): Docker.compose_build(SERVICES_PATH, s_name)
        elif image == f"foundation/{s_name}":
            with console.status(f"Building [bold italic]{s_name}[/] from source..."):
                Railpack.prepare(s_dir, s_dir / "railpack-plan.json")
                Docker.build_from_railpack_plan(f"foundation/{s_name}", s_dir, s_dir / "railpack-plan.json")
        else:
            with console.status(f"Pulling [bold italic]{s_name}[/]..."): Docker.compose_pull(SERVICES_PATH, s_name)

    with console.status("Starting reverse proxy..."):
        try: Docker.compose_up(PROXY_PATH)
        except Exception as e: Output.error("Could not start reverse proxy", exception=e)

    if not services:
        with console.status("Updating services..."): Docker.compose_down(SERVICES_PATH)
        if report_success: Output.success("Deployment complete", "view running services", "status")
        return

    with console.status(f"{'Starting' if name in services else 'Updating'} services..."):
        try: Docker.compose_up(SERVICES_PATH)
        except Exception as e: Output.error("Could not start services", exception=e)
        if report_success: Output.success("Deployment complete", "view running services", "status")
        
    subprocess.run(["docker", "image", "prune", "-f"], capture_output=True) # Cleanup detached layers silently

@app.command(help="Stream live logs for a service.")
def logs(name: Annotated[str, typer.Argument()], follow: bool = typer.Option(True, "--follow", "-f")):
    cmd = ["docker", "compose", "-f", SERVICES_PATH, "logs", "--tail=100"]
    if follow: cmd.append("-f")
    cmd.append(name)
    subprocess.run(cmd)

@app.command(help="Drop into a service's terminal.")
def shell(name: Annotated[str, typer.Argument()]):
    if subprocess.run(["docker", "exec", "-it", name, "/bin/bash"]).returncode != 0:
        subprocess.run(["docker", "exec", "-it", name, "/bin/sh"])

@app.command(help="Execute a one-off command inside a service.")
def exec(name: Annotated[str, typer.Argument()], command: Annotated[str, typer.Argument(help="Command to run (e.g. 'ls -la')")]):
    subprocess.run(["docker", "exec", "-it", name] + command.split())

# --- DOMAIN SUB-APP ---

@domain_app.command("add", help="Attach a domain to a service.")
def domain_add(name: str, domain: str, port: int = typer.Option(None, "-p", "--port"), email: str = typer.Option(None, "--email")):
    services_compose = Docker.get_compose(SERVICES_PATH)
    service = services_compose.get("services", {}).get(name)
    if not service: Output.error(f"Service [bold italic]{name}[/] not found")

    env = service.setdefault("environment", {})
    for key in ["VIRTUAL_HOST", "LETSENCRYPT_HOST"]:
        hosts = set(filter(None, env.get(key, "").split(",")))
        hosts.add(domain)
        env[key] = ",".join(sorted(hosts))

    if port: env["VIRTUAL_PORT"] = str(port)
    if email: env["LETSENCRYPT_EMAIL"] = email

    with console.status("Updating..."): Docker.write_compose(SERVICES_PATH, services_compose)
    deploy(name, report_success=False)
    Output.success(f"Domain [bold cyan]{domain}[/] attached to [bold italic]{name}[/]")

@domain_app.command("remove", help="Detach a domain from a service.")
def domain_remove(name: str, domain: str):
    services_compose = Docker.get_compose(SERVICES_PATH)
    service = services_compose.get("services", {}).get(name)
    if not service: Output.error(f"Service [bold italic]{name}[/] not found")

    env = service.get("environment", {})
    for key in ["VIRTUAL_HOST", "LETSENCRYPT_HOST"]:
        hosts = set(filter(None, env.get(key, "").split(",")))
        if domain in hosts:
            hosts.remove(domain)
            if hosts: env[key] = ",".join(sorted(hosts))
            else: env.pop(key, None)

    with console.status("Updating..."): Docker.write_compose(SERVICES_PATH, services_compose)
    deploy(name, report_success=False)
    Output.success(f"Domain [bold cyan]{domain}[/] removed from [bold italic]{name}[/]")

@domain_app.command("redirect", help="Create a permanent redirect.")
def domain_redirect(from_domain: str, target_url: str, email: str = typer.Option(None, "--email")):
    if not target_url.startswith(("http://", "https://")): target_url = f"https://{target_url}"
    services_compose = Docker.get_compose(SERVICES_PATH)
    safe_name = f"redirect-{from_domain.replace('.', '-')}"
    if safe_name in services_compose.get("services", {}): Output.error(f"Redirect for [bold]{from_domain}[/] exists")

    nginx_conf = f"server {{ listen 80; return 301 {target_url.rstrip('/')}$$request_uri; }}"
    services_compose.setdefault("services", {})[safe_name] = {
        "container_name": safe_name, "image": "nginx:alpine",
        "command": ["/bin/sh", "-c", f"echo '{nginx_conf}' > /etc/nginx/conf.d/default.conf && nginx -g 'daemon off;'"],
        "environment": {"VIRTUAL_HOST": from_domain, "LETSENCRYPT_HOST": from_domain, **({"LETSENCRYPT_EMAIL": email} if email else {})},
        "networks": ["foundation_network"], "restart": "unless-stopped"
    }
    with console.status("Updating..."): Docker.write_compose(SERVICES_PATH, services_compose)
    deploy(safe_name, report_success=False)
    Output.success(f"Redirect [bold cyan]{from_domain}[/] ➔ [bold cyan]{target_url}[/] created")

# --- ENV SUB-APP ---

@env_app.command("add", help="Add or update environment variables.")
def env_add(name: str, vars: Annotated[list[str], typer.Argument(help="KEY=VALUE pairs")]):
    services_compose = Docker.get_compose(SERVICES_PATH)
    service = services_compose.get("services", {}).get(name)
    if not service: Output.error(f"Service [bold italic]{name}[/] not found")

    env = service.setdefault("environment", {})
    for v in vars:
        if "=" not in v: Output.error(f"Invalid format for '{v}'", "use KEY=VALUE")
        k, val = v.split("=", 1)
        env[k] = val

    with console.status("Updating..."): Docker.write_compose(SERVICES_PATH, services_compose)
    deploy(name, report_success=False)
    Output.success(f"Environment updated for [bold italic]{name}[/]")

@env_app.command("remove", help="Remove an environment variable.")
def env_remove(name: str, key: str):
    services_compose = Docker.get_compose(SERVICES_PATH)
    service = services_compose.get("services", {}).get(name)
    if not service: Output.error(f"Service [bold italic]{name}[/] not found")

    if key in service.get("environment", {}):
        service["environment"].pop(key)
        if not service["environment"]: service.pop("environment")
        with console.status("Updating..."): Docker.write_compose(SERVICES_PATH, services_compose)
        deploy(name, report_success=False)
        Output.success(f"Removed [bold]{key}[/] from [bold italic]{name}[/]")
    else:
        Output.info(f"Variable [bold]{key}[/] not found in [bold italic]{name}[/]")

# --- VOLUME SUB-APP ---

@volume_app.command("add", help="Mount a persistent volume.")
def volume_add(name: str, volume_map: Annotated[str, typer.Argument(help="Format: volume_name:/container/path")]):
    if ":" not in volume_map or volume_map.startswith(("/", ".", "~")): Output.error("Invalid volume format", "use name:/path")
    vol_name, vol_path = volume_map.split(":", 1)
    
    services_compose = Docker.get_compose(SERVICES_PATH)
    service = services_compose.get("services", {}).get(name)
    if not service: Output.error(f"Service [bold italic]{name}[/] not found")

    vols = service.setdefault("volumes", [])
    if volume_map not in vols: vols.append(volume_map)
    services_compose.setdefault("volumes", {})[vol_name] = {}

    with console.status("Updating..."): Docker.write_compose(SERVICES_PATH, services_compose)
    deploy(name, report_success=False)
    Output.success(f"Volume [bold]{vol_name}[/] mounted to [bold italic]{name}[/]")

@volume_app.command("remove", help="Unmount a volume.")
def volume_remove(name: str, volume_name: str):
    services_compose = Docker.get_compose(SERVICES_PATH)
    service = services_compose.get("services", {}).get(name)
    if not service: Output.error(f"Service [bold italic]{name}[/] not found")

    vols = service.get("volumes", [])
    service["volumes"] = [v for v in vols if not v.startswith(f"{volume_name}:")]
    if not service["volumes"]: service.pop("volumes")

    with console.status("Updating..."): Docker.write_compose(SERVICES_PATH, services_compose)
    deploy(name, report_success=False)
    Output.success(f"Volume [bold]{volume_name}[/] removed from [bold italic]{name}[/]")

if __name__ == "__main__":
    app()