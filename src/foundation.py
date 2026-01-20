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
from rich.style import Style

APP_NAME = "foundation"
app_dir = typer.get_app_dir(APP_NAME)
foundation_compose_path = Path(app_dir) / "compose.json"
services_path = Path(app_dir) / "services"
services_compose_path = services_path / "compose.json"

console = Console()

app = typer.Typer(
    name="foundation",
    help="Manage Docker services with automatic reverse proxying.",
    no_args_is_help=True,
    add_completion=False
)

class Output:
    @staticmethod
    def info(text):
        console.print(text)

    @staticmethod
    def success(text):
        console.print(f"[bold green]Success:[/] {text}")

    @staticmethod
    def error(text, exit = True):
        console.print(f"[bold red]Error:[/] {text}")
        if exit:
            raise typer.Exit(code=1)

    @staticmethod
    def step(text):
        console.print(f"[bold white]>[/] {text}")

def check_ports_available():
    ports = [80, 443]
    for port in ports:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(1)
            try:
                sock.bind(("0.0.0.0", port))
            except OSError:
                Output.error(f"Port {port} is in use. Stop conflicting web servers (Apache/Nginx) and try again.")
                return False
    return True

def get_foundation_compose():
    if not foundation_compose_path.is_file(): return
    result = subprocess.run(["docker", "compose", "-f", foundation_compose_path, "config"], capture_output=True)
    if result.returncode != 0: return
    return json.loads(foundation_compose_path.read_text())

def get_services_compose():
    if not services_compose_path.is_file(): return
    result = subprocess.run(["docker", "compose", "-f", services_compose_path, "config"], capture_output=True)
    if result.returncode != 0: return
    return json.loads(services_compose_path.read_text())

def docker_installed():
    return subprocess.run(["docker", "--version"], capture_output=True).returncode == 0

def docker_running():
    return subprocess.run(["docker", "info"], capture_output=True).returncode == 0

def docker_permissions():
    result = subprocess.run(["docker", "info"], capture_output=True, text=True)
    return result.returncode == 0 or "permission denied" in result.stderr

def git_installed():
    return subprocess.run(["git", "--version"], capture_output=True).returncode == 0

def railpack_installed():
    return subprocess.run(["railpack", "--version"], capture_output=True).returncode == 0

def foundation_installed():
    return bool(get_foundation_compose()) and bool(get_services_compose())

def foundation_running():
    fc = get_foundation_compose()
    if not fc: return False
    services = fc.get("services", {}).keys()
    
    cmd = ["docker", "compose", "-f", foundation_compose_path, "ps", "-a", "--format", "json"]
    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    
    running = {
        s["Service"] for s in map(json.loads, result.stdout.splitlines()) 
        if s.get("State") == "running"
    }
    return services.issubset(running)

def install_dependency(name: str, url: str, script_name: str):
    with console.status(f"Installing {name}..."):
        subprocess.run(["curl", "-fsSL", url, "-o", script_name], check=True)
        subprocess.run(["sh", script_name], check=True)
        Path(script_name).unlink(missing_ok=True)
    Output.success(f"{name} installed.")

def check_docker():
    if not docker_running():
        Output.error("Docker is not running.")
    if not docker_permissions():
        Output.error("Permission denied. Ensure you have Docker privileges.")

def is_repo(source):
    try:
        subprocess.run(["git", "ls-remote", source], check=True, timeout=10, capture_output=True)
        return True
    except:
        return False

def is_image(source):
    try:
        subprocess.run(["docker", "manifest", "inspect", source], check=True, timeout=10, capture_output=True)
        return True
    except:
        return False

def build_from_dir(service_path):
    name = service_path.name
    service_dockerfile_path = service_path / "Dockerfile"

    if service_dockerfile_path.is_file():
        with console.status(f"Building '{name}' from Dockerfile..."):
            subprocess.run(["docker", "build", "-t", f"foundation/{name}", service_path], capture_output=True, check=True)
        Output.success(f"Built '{name}'.")
        return
            
    with console.status(f"Building '{name}' using Railpack..."):
        # Ensure builder exists
        res = subprocess.run(["docker", "buildx", "use", "railpack-builder"], capture_output=True)
        if res.returncode != 0:
            subprocess.run(["docker", "buildx", "create", "--name", "railpack-builder", "--driver", "docker-container", "--use"], capture_output=True, check=True)
        
        subprocess.run(["docker", "buildx", "inspect", "--bootstrap"], capture_output=True, check=True)

        # Plan and build
        plan_path = service_path / f"{name}-railpack-plan.json"
        subprocess.run(["railpack", "plan", service_path, "-o", plan_path], capture_output=True, check=True)
        
        cmd = [
            "docker", "buildx", "build", 
            "--build-arg", "BUILDKIT_SYNTAX=ghcr.io/railwayapp/railpack-frontend", 
            "-f", plan_path, "--load", 
            "-t", f"foundation/{name}", service_path
        ]
        subprocess.run(cmd, capture_output=True, check=True)
        plan_path.unlink(missing_ok=True)
    
    Output.success(f"Built '{name}'.")

@app.callback()
def main(ctx: typer.Context):
    if ctx.invoked_subcommand == "install": return

    if not docker_installed():
        Output.error("Docker missing. Run 'foundation install'.")
    
    check_docker()

    if not git_installed():
        Output.error("Git missing. Run 'foundation install'.")
    
    if not foundation_installed():
        Output.error("Foundation not configured. Run 'foundation install'.")
    
    if ctx.invoked_subcommand == "deploy": return

    if not foundation_running():
        Output.error("Foundation core is down. Run 'foundation deploy'.")

@app.command(help="Setup environment and dependencies.")
def install(
    default_email: Annotated[str, typer.Option(help="Email for SSL certificate registration.")] = None
):
    services_compose = get_services_compose() or {}
    services = services_compose.get("services", {})
    volumes = services_compose.get("volumes", {})

    check_ports_available()

    if not docker_installed(): 
        install_dependency("Docker", "https://get.docker.com", "get-docker.sh")
    if not git_installed(): 
        install_dependency("Git", "https://raw.githubusercontent.com/ElliottStorey/git-install/main/install.sh", "get-git.sh")
    if not railpack_installed(): 
        install_dependency("Railpack", "https://railpack.com/install.sh", "get-railpack.sh")

    with console.status("Configuring Foundation..."):
        foundation_compose = {
            "name": "foundation",
            "services": {
                "nginx-proxy": {
                    "container_name": "nginx-proxy",
                    "image": "nginxproxy/nginx-proxy",
                    "volumes": [
                        "certs:/etc/nginx/certs",
                        "html:/usr/share/nginx/html",
                        "/var/run/docker.sock:/tmp/docker.sock:ro"
                    ],
                    "ports": ["80:80", "443:443"],
                    "networks": ["foundation_network"],
                    "restart": "unless-stopped"
                },
                "nginx-proxy-acme": {
                    "container_name": "nginx-proxy-acme",
                    "image": "nginxproxy/acme-companion",
                    "environment": [f"DEFAULT_EMAIL={default_email}"],
                    "volumes": ["acme:/etc/acme.sh"],
                    "volumes_from": ["nginx-proxy"],
                    "networks": ["foundation_network"],
                    "restart": "unless-stopped"
                }
            },
            "volumes": {"certs": {}, "html": {}, "acme": {}},
            "networks": {"foundation_network": {"name": "foundation_network"}}
        }

        services_compose = {
            "name": "foundation services",
            "services": services,
            "volumes": volumes,
            "networks": {
                "foundation_network": {
                    "external": True,
                    "name": "foundation_network"
                }
            }
        }

        foundation_compose_path.parent.mkdir(parents=True, exist_ok=True)
        foundation_compose_path.write_text(json.dumps(foundation_compose, indent=2), encoding="utf-8")
        services_compose_path.parent.mkdir(parents=True, exist_ok=True)
        services_compose_path.write_text(json.dumps(services_compose, indent=2), encoding="utf-8")

    Output.success("Installation complete. Run 'foundation create' to add a service.")
    deploy()

@app.command(help="Pull latest changes and rebuild services.", hidden=True)
def update():
    for service_path in services_path.iterdir():
        if not service_path.is_dir(): continue
        if not (service_path / ".git").is_dir(): continue

        name = service_path.name
        
        # Git Check
        subprocess.run(["git", "fetch"], cwd=service_path, capture_output=True, check=True)
        local = subprocess.run(["git", "rev-parse", "HEAD"], cwd=service_path, capture_output=True, text=True, check=True).stdout.strip()
        upstream = subprocess.run(["git", "rev-parse", "@{u}"], cwd=service_path, capture_output=True, text=True, check=True).stdout.strip()

        if local != upstream:
            Output.info(f"Updating '{name}'...")
            with console.status("Pulling changes..."):
                subprocess.run(["git", "reset", "--hard", "@{u}"], cwd=service_path, capture_output=True, check=True)
            build_from_dir(service_path)
    
    with console.status("Updating registry images..."):
        subprocess.run(["docker", "compose", "-f", services_compose_path, "pull"], capture_output=True, check=True)
        subprocess.run(["docker", "compose", "-f", services_compose_path, "build"], capture_output=True, check=True)

    Output.success("All services updated. Run 'foundation deploy' to apply.")

@app.command(help="Start core system and services.", hidden=True)
def deploy():
    services_compose = get_services_compose()

    if not foundation_running():
        check_ports_available()

    with console.status("Starting Foundation core..."):
        subprocess.run(["docker", "compose", "-f", foundation_compose_path, "up", "-d"], capture_output=True, check=True)
    
    Output.info("Foundation core active.")

    if not services_compose.get("services"):
        Output.info("No services defined. Run 'foundation create'.")
        raise typer.Exit()

    with console.status("Starting services..."):
        subprocess.run(["docker", "compose", "-f", services_compose_path, "up", "-d", "--remove-orphans"], capture_output=True, check=True)
    
    Output.success("Deployment complete.")

@app.command(help="Show service status.")
def status():
    services_compose = get_services_compose()
    services = services_compose.get("services", {})
    names = services.keys()

    if not services:
        Output.info("No services defined.")
        raise typer.Exit()

    table = Table(box=None, header_style="bold blue")
    table.add_column("Service")
    table.add_column("Status")
    table.add_column("Domain")
    table.add_column("Created")

    for name in names:
        created_at = "-"
        status = "[dim]Down[/]"
        host = "-"

        cmd = ["docker", "compose", "-f", services_compose_path, "ps", "-a", name, "--format", "json"]
        result = subprocess.run(cmd, capture_output=True, text=True)
        
        if result.stdout.strip():
            info = json.loads(result.stdout.splitlines()[0])
            created_at = info.get("CreatedAt", "-").split(" ")[0] # Just date
            raw_state = info.get("State", "unknown")
            
            if raw_state.lower() == "running":
                status = "[green]Running[/]"
            else:
                status = f"[red]{raw_state}[/]"
        
        env_vars = services[name].get("environment", [])
        host_env = next((e for e in env_vars if e.startswith("VIRTUAL_HOST=")), None)
        if host_env: 
            host = host_env.split("=", 1)[1]

        table.add_row(name, status, host, created_at)

    console.print(table)

class RestartPolicy(str, Enum):
    no = "no"
    always = "always"
    on_failure = "on-failure"
    unless_stopped = "unless-stopped"

@app.command(help="Create a new service.")
def create(
    name: Annotated[str, typer.Argument(help="Unique service name.")],
    source: Annotated[str, typer.Option("--repo", "--image", help="Git URL or Docker image.", prompt="Source (Repo URL or Image)")],
    host: Annotated[str, typer.Option(help="Domain (VIRTUAL_HOST).")] = None,
    port: Annotated[int, typer.Option(help="Internal container port.")] = 80,
    letsencrypt_email: Annotated[str, typer.Option(help="SSL email.")] = None,
    environment: Annotated[list[str], typer.Option("--env", "-e", help="KEY=VALUE variables.")] = [],
    volumes: Annotated[list[str], typer.Option("--volume", "-v", help="NAME:PATH mappings.")] = [],
    restart_policy: Annotated[RestartPolicy, typer.Option("--restart", help="Restart policy.")] = RestartPolicy.unless_stopped,
    gpu: Annotated[bool, typer.Option("--gpu", help="Enable GPU.")] = False
):
    service_path = services_path / name
    services_compose = get_services_compose()

    if name in services_compose.get("services", {}):
        Output.error(f"Service '{name}' already exists.")
    
    for volume in volumes:
        v_name, _ = volume.split(":", 1)
        if v_name.startswith(("/", ".", "~")):
            Output.error("Volume name cannot be a path.")
        services_compose["volumes"][v_name] = {}

    service_conf = {
        "container_name": name,
        "image": None,
        "environment": environment,
        "volumes": volumes,
        "networks": ["foundation_network"],
        "restart": restart_policy.value
    }

    if host:
        service_conf["environment"].extend([
            f"VIRTUAL_HOST={host}",
            f"VIRTUAL_PORT={port}",
            f"LETSENCRYPT_HOST={host}"
        ])
        if letsencrypt_email: 
            service_conf["environment"].append(f"LETSENCRYPT_EMAIL={letsencrypt_email}")

    if gpu:
        service_conf["deploy"] = {
            "resources": {
                "reservations": {
                    "devices": [{"driver": "nvidia", "count": "all", "capabilities": ["gpu"]}]
                }
            }
        }

    if is_repo(source):
        service_conf["image"] = f"foundation/{name}"
        with console.status(f"Cloning {source}..."):
            subprocess.run(["git", "clone", source, service_path], capture_output=True, check=True)
        build_from_dir(service_path)
    elif is_image(source):
        service_conf["image"] = source
    else:
        Output.error(f"'{source}' is not a valid Git repo or Docker image.")

    services_compose["services"][name] = service_conf
    services_compose_path.write_text(json.dumps(services_compose, indent=2), encoding="utf-8")

    Output.success(f"Service '{name}' created.")
    deploy()

@app.command(help="Remove a service.")
def delete(name: Annotated[str, typer.Argument(help="Service to delete.")]):
    service_path = services_path / name
    services_compose = get_services_compose()

    if name not in services_compose.get("services", {}):
        Output.error(f"Service '{name}' not found.")

    del services_compose.get("services", {})[name]
    services_compose_path.write_text(json.dumps(services_compose, indent=2), encoding="utf-8")
    
    Output.info(f"Configuration removed for '{name}'.")
    
    deploy()

    if service_path.exists():
        shutil.rmtree(service_path)
        Output.info(f"Local files removed for '{name}'.")

    Output.success(f"Service '{name}' deleted.")

if __name__ == "__main__":
    app()