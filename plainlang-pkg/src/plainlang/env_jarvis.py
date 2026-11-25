import io
import importlib
import importlib.metadata as importlib_metadata
import inspect
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from datetime import datetime

ENV_DIR_NAME = ".pvenv"
DEFAULT_REGISTRY_URL = os.environ.get("JARVIS_REGISTRY_URL", "")
PYPI_JSON_URL = "https://pypi.org/pypi/{pkg}/json"


def find_pvenv(start_dir=None):
    curr = os.path.abspath(start_dir or os.getcwd())
    while True:
        candidate = os.path.join(curr, ENV_DIR_NAME)
        if os.path.isdir(candidate) and os.path.isfile(os.path.join(candidate, "pvenv.cfg")):
            return candidate
        parent = os.path.dirname(curr)
        if parent == curr:
            return None
        curr = parent


def ensure_pvenv(start_dir=None, plainlang_version="4.0.0"):
    existing = find_pvenv(start_dir)
    if existing:
        env_path = existing
    else:
        root = os.path.abspath(start_dir or os.getcwd())
        env_path = os.path.join(root, ENV_DIR_NAME)
        os.makedirs(os.path.join(env_path, "packages"), exist_ok=True)
        os.makedirs(os.path.join(env_path, "scripts"), exist_ok=True)

        cfg = {
            "name": ENV_DIR_NAME,
            "language": "PlainLang",
            "version": plainlang_version,
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "packages_dir": "packages",
        }
        with open(os.path.join(env_path, "pvenv.cfg"), "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2)

        manifest_path = os.path.join(env_path, "installed.json")
        if not os.path.exists(manifest_path):
            with open(manifest_path, "w", encoding="utf-8") as f:
                json.dump({"packages": {}}, f, indent=2)

    venv_scripts_dir = os.path.join(env_path, "Scripts")
    os.makedirs(venv_scripts_dir, exist_ok=True)
    activate_ps1 = os.path.join(venv_scripts_dir, "Activate.ps1")
    if not os.path.exists(activate_ps1):
        ps_script = (
            '$env:PVENV_DIR = Split-Path -Parent $PSScriptRoot\n'
            'function global:prompt {\n'
            '    "(.pvenv) PS $($executionContext.SessionState.Path.CurrentLocation)> "\n'
            '}\n'
            'function global:deactivate_pvenv {\n'
            '    Remove-Item Env:PVENV_DIR -ErrorAction SilentlyContinue\n'
            '    function global:prompt {\n'
            '        "PS $($executionContext.SessionState.Path.CurrentLocation)> "\n'
            '    }\n'
            '    Remove-Item function:global:deactivate_pvenv -Force -ErrorAction SilentlyContinue\n'
            '}\n'
        )
        with open(activate_ps1, "w", encoding="utf-8") as f:
            f.write(ps_script)

    return env_path


class JarvisError(Exception):
    pass


class JarvisInstaller:
    def __init__(self, env_path=None, registry_url=None, verbose=True):
        self.env_path = env_path or ensure_pvenv()
        self.packages_dir = os.path.join(self.env_path, "packages")
        self.manifest_path = os.path.join(self.env_path, "installed.json")
        self.registry_url = registry_url if registry_url is not None else DEFAULT_REGISTRY_URL
        self.verbose = verbose
        self.python_exe = self._find_target_python()
        self.site_packages_dirs = self._get_site_packages_dirs()
        os.makedirs(self.packages_dir, exist_ok=True)

    def _log(self, msg):
        if self.verbose:
            print(msg, flush=True)

    def _find_target_python(self):
        project_root = os.path.dirname(self.env_path)
        for venv_name in (".venv", "venv"):
            if os.name == "nt":
                candidate = os.path.join(project_root, venv_name, "Scripts", "python.exe")
            else:
                candidate = os.path.join(project_root, venv_name, "bin", "python")
            if os.path.isfile(candidate):
                return candidate
        return sys.executable

    def _get_site_packages_dirs(self):
        if os.path.normcase(os.path.abspath(self.python_exe)) == os.path.normcase(os.path.abspath(sys.executable)):
            return list(sys.path)
        try:
            proc = subprocess.run(
                [self.python_exe, "-c", "import site, json; print(json.dumps(site.getsitepackages()))"],
                capture_output=True,
                text=True,
                check=False,
            )
            if proc.returncode == 0 and proc.stdout.strip():
                dirs = json.loads(proc.stdout.strip())
                return [d for d in dirs if os.path.isdir(d)]
        except Exception:
            pass
        return []

    def _load_manifest(self):
        if not os.path.exists(self.manifest_path):
            return {"packages": {}}
        with open(self.manifest_path, "r", encoding="utf-8") as f:
            return json.load(f)

    def _save_manifest(self, data):
        with open(self.manifest_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

    def _resolve_package_key(self, packages, pkg_name):
        if pkg_name in packages:
            return pkg_name
        target_low = pkg_name.strip().lower()
        for existing_key in packages:
            if existing_key.lower() == target_low:
                return existing_key
        return None

    def _read_package_meta(self, pkg_dir, fallback_name):
        for meta_name in ("jarvis.json", "plainpkg.json"):
            meta_file = os.path.join(pkg_dir, meta_name)
            if os.path.isfile(meta_file):
                with open(meta_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                return (
                    data.get("name") or fallback_name,
                    data.get("version") or "0.1.0",
                    data.get("dependencies") or [],
                )
        return fallback_name, "0.1.0", []

    def _lookup_registry(self, pkg_name):
        if not self.registry_url:
            return None
        self._log(f"[jarvis] Checking registry for '{pkg_name}'...")
        try:
            if os.path.isfile(self.registry_url):
                with open(self.registry_url, "r", encoding="utf-8") as f:
                    reg = json.load(f)
            else:
                with urllib.request.urlopen(self.registry_url, timeout=10) as resp:
                    reg = json.loads(resp.read().decode("utf-8"))
        except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
            raise JarvisError(f"Failed to fetch registry '{self.registry_url}': {exc}")

        packages = reg.get("packages", reg)
        if isinstance(packages, dict) and pkg_name in packages:
            entry = packages[pkg_name]
            return entry.get("url") if isinstance(entry, dict) else entry
        return None

    def _lookup_pypi(self, pkg_name):
        pkg_clean = pkg_name.split("==")[0].split(">=")[0].split("<=")[0].split("[")[0].strip()
        self._log(f"[jarvis] Resolving '{pkg_clean}' on PyPI...")
        url = PYPI_JSON_URL.format(pkg=urllib.parse.quote(pkg_clean))
        try:
            with urllib.request.urlopen(url, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            info = data.get("info", {})
            return {
                "name": info.get("name") or pkg_clean,
                "version": info.get("version") or "latest",
                "spec": pkg_name,
            }
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return None
            raise JarvisError(f"PyPI lookup failed for '{pkg_name}': HTTP {exc.code}")
        except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
            raise JarvisError(f"PyPI connection error for '{pkg_name}': {exc}")

    def _get_dist_info(self, dist_name):
        search_paths = self.site_packages_dirs + [p for p in sys.path if p not in self.site_packages_dirs]
        try:
            dists = importlib_metadata.distributions(path=search_paths)
            target_norm = re.sub(r"[-_.]+", "-", dist_name).lower()
            for dist in dists:
                meta_name = dist.metadata.get("Name", "")
                if re.sub(r"[-_.]+", "-", meta_name).lower() == target_norm:
                    return dist
        except Exception:
            pass
        return None

    def _resolve_installed_dependencies(self, dist_name):
        dist = self._get_dist_info(dist_name)
        if not dist or not dist.requires:
            return []

        deps = []
        for req_str in dist.requires:
            if ";" in req_str:
                req_part, marker_part = req_str.split(";", 1)
                if "extra" in marker_part:
                    continue
                try:
                    from packaging.markers import Marker
                    if not Marker(marker_part.strip()).evaluate():
                        continue
                except Exception:
                    pass
            else:
                req_part = req_str

            match = re.match(r"^\s*([A-Za-z0-9_.-]+)", req_part)
            if match:
                dep_name = match.group(1)
                if dep_name.lower() != dist_name.lower() and dep_name not in deps:
                    deps.append(dep_name)
        return deps

    def _resolve_import_name(self, pkg_name):
        dist = self._get_dist_info(pkg_name)
        if dist:
            top_level = dist.read_text("top_level.txt")
            if top_level:
                names = [line.strip() for line in top_level.splitlines() if line.strip() and not line.startswith("_")]
                if names:
                    return names[0]
        return pkg_name.lower().replace("-", "_")

    def _generate_plain_bridge(self, pkg_name, version, source_path=None):
        import_name = self._resolve_import_name(pkg_name)
        plain_filename = f"{import_name}.plain"
        plain_path = os.path.join(self.packages_dir, plain_filename)
        self._log(f"[jarvis] Generating '{plain_filename}'...")

        actions = []
        loaded_mod = None
        added_paths = []

        candidate_paths = []
        if source_path and os.path.isdir(source_path):
            candidate_paths.append(source_path)
        for sp in self.site_packages_dirs:
            if sp not in sys.path:
                candidate_paths.append(sp)

        for p in reversed(candidate_paths):
            sys.path.insert(0, p)
            added_paths.append(p)

        try:
            loaded_mod = importlib.import_module(import_name)
        except Exception:
            loaded_mod = None
        finally:
            for p in added_paths:
                if p in sys.path:
                    sys.path.remove(p)

        if loaded_mod is not None:
            for attr_name in dir(loaded_mod):
                if attr_name.startswith("_"):
                    continue
                obj = getattr(loaded_mod, attr_name, None)
                if not inspect.isfunction(obj):
                    continue
                try:
                    sig = inspect.signature(obj)
                    params = [
                        p.name
                        for p in sig.parameters.values()
                        if p.kind in (
                            inspect.Parameter.POSITIONAL_ONLY,
                            inspect.Parameter.POSITIONAL_OR_KEYWORD,
                        )
                    ]
                except (ValueError, TypeError):
                    params = []
                if len(actions) >= 15:
                    break
                if params:
                    param_words = " ".join(params)
                    actions.append(
                        f"to {attr_name} with {param_words}\n"
                        f"    call {import_name} {attr_name} with {param_words}"
                    )
                else:
                    actions.append(
                        f"to {attr_name}\n"
                        f"    call {import_name} {attr_name}"
                    )

        lines = [
            f"use {import_name}",
            f"set {import_name} version to {version}",
        ]
        if actions:
            lines.append("")
            lines.extend(actions)

        with open(plain_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")

        self._log(f"[jarvis] Wrote {plain_path}")
        return plain_path

    def _sync_pypi_dependency_tree(self, root_pkg_name, manifest, visited):
        queue = [root_pkg_name]
        while queue:
            curr_name = queue.pop(0)
            norm_curr = curr_name.lower()
            if norm_curr in visited:
                continue
            visited.add(norm_curr)

            dist = self._get_dist_info(curr_name)
            canonical_name = dist.metadata.get("Name", curr_name) if dist else curr_name
            version = dist.version if dist else "latest"
            deps = self._resolve_installed_dependencies(canonical_name)

            plain_path = self._generate_plain_bridge(canonical_name, version)
            packages = manifest.setdefault("packages", {})
            existing_key = self._resolve_package_key(packages, canonical_name) or canonical_name

            is_root = (norm_curr == root_pkg_name.lower())
            packages[existing_key] = {
                "type": "plain-pypi" if is_root else "plain-pypi-dep",
                "version": version,
                "source": f"pypi:{canonical_name}" if is_root else f"pypi-dep:{root_pkg_name}",
                "path": plain_path,
                "dependencies": deps,
                "installed_at": datetime.now().isoformat(timespec="seconds"),
            }

            for dep in deps:
                if dep.lower() not in visited:
                    self._log(f"[jarvis] Converting dependency '{dep}' to .plain...")
                    queue.append(dep)

    def _install_from_pypi(self, pkg_spec, _visited=None):
        visited = _visited if _visited is not None else set()
        meta = self._lookup_pypi(pkg_spec)
        if not meta:
            raise JarvisError(
                f"Package '{pkg_spec}' not found locally, in registry, or on PyPI."
            )

        pkg_name = meta["name"]
        version = meta["version"]
        install_spec = meta["spec"]

        self._log(f"[jarvis] Installing {pkg_name} (v{version}) via pip into {self.python_exe}...")
        proc = subprocess.run(
            [self.python_exe, "-m", "pip", "install", install_spec],
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode != 0:
            err_msg = (proc.stderr or proc.stdout or "").strip()
            raise JarvisError(f"pip failed to install '{install_spec}' from PyPI: {err_msg}")

        self.site_packages_dirs = self._get_site_packages_dirs()
        self._log(f"[jarvis] pip install complete for {pkg_name} (v{version}). Resolving dependencies...")

        manifest = self._load_manifest()
        self._sync_pypi_dependency_tree(pkg_name, manifest, visited)

        self._log(f"[jarvis] Updating {self.manifest_path}...")
        self._save_manifest(manifest)

        packages = manifest.get("packages", {})
        matched_key = self._resolve_package_key(packages, pkg_name) or pkg_name
        return matched_key, packages[matched_key]

    def _download_bytes(self, url):
        self._log(f"[jarvis] Downloading '{url}'...")
        try:
            with urllib.request.urlopen(url, timeout=15) as resp:
                return resp.read()
        except (OSError, urllib.error.URLError) as exc:
            raise JarvisError(f"Failed to download '{url}': {exc}")

    def _install_zip_bytes(self, raw_bytes, pkg_name, source_label):
        dest = os.path.join(self.packages_dir, pkg_name)
        self._log(f"[jarvis] Extracting archive to '{dest}'...")
        if os.path.exists(dest):
            shutil.rmtree(dest)
        os.makedirs(dest, exist_ok=True)

        with zipfile.ZipFile(io.BytesIO(raw_bytes)) as zf:
            zf.extractall(dest)

        entries = os.listdir(dest)
        if len(entries) == 1 and os.path.isdir(os.path.join(dest, entries[0])):
            inner = os.path.join(dest, entries[0])
            for item in os.listdir(inner):
                shutil.move(os.path.join(inner, item), os.path.join(dest, item))
            os.rmdir(inner)

        name, version, deps = self._read_package_meta(dest, pkg_name)
        plain_path = self._generate_plain_bridge(name, version, source_path=dest)
        return name, {
            "type": "zip",
            "version": version,
            "source": source_label,
            "path": plain_path,
            "package_dir": dest,
            "dependencies": deps,
            "installed_at": datetime.now().isoformat(timespec="seconds"),
        }

    def install(self, target, _visited=None):
        visited = _visited if _visited is not None else set()
        target = str(target).strip()
        if not target:
            raise JarvisError("No package target provided.")

        self._log(f"[jarvis] Installation in progress for '{target}'...")
        manifest = self._load_manifest()
        packages = manifest.setdefault("packages", {})
        deps = []

        if os.path.exists(target):
            src = os.path.abspath(target)
            base_name = os.path.splitext(os.path.basename(src.rstrip("\\/")))[0]
            self._log(f"[jarvis] Installing local target '{src}'...")

            if os.path.isdir(src):
                dest = os.path.join(self.packages_dir, base_name)
                if os.path.exists(dest):
                    shutil.rmtree(dest)
                shutil.copytree(src, dest)
                name, version, deps = self._read_package_meta(dest, base_name)
                plain_path = self._generate_plain_bridge(name, version, source_path=dest)
                packages[name] = {
                    "type": "folder",
                    "version": version,
                    "source": src,
                    "path": plain_path,
                    "package_dir": dest,
                    "dependencies": deps,
                    "installed_at": datetime.now().isoformat(timespec="seconds"),
                }
            elif src.lower().endswith(".zip"):
                with open(src, "rb") as f:
                    name, record = self._install_zip_bytes(f.read(), base_name, src)
                packages[name] = record
                deps = record["dependencies"]
            else:
                name = base_name
                version = "0.1.0"
                deps = []
                plain_path = self._generate_plain_bridge(name, version, source_path=src)
                packages[name] = {
                    "type": "script",
                    "version": version,
                    "source": src,
                    "path": plain_path,
                    "dependencies": deps,
                    "installed_at": datetime.now().isoformat(timespec="seconds"),
                }

        elif target.startswith(("http://", "https://")):
            clean_name = os.path.splitext(os.path.basename(target.split("?")[0]))[0] or "remote_pkg"
            data = self._download_bytes(target)
            if target.lower().split("?")[0].endswith(".zip"):
                name, record = self._install_zip_bytes(data, clean_name, target)
                packages[name] = record
                deps = record["dependencies"]
            else:
                name = clean_name
                version = "0.1.0"
                deps = []
                plain_path = self._generate_plain_bridge(name, version)
                packages[name] = {
                    "type": "remote-script",
                    "version": version,
                    "source": target,
                    "path": plain_path,
                    "dependencies": deps,
                    "installed_at": datetime.now().isoformat(timespec="seconds"),
                }

        else:
            reg_url = self._lookup_registry(target)
            if reg_url:
                self._log(f"[jarvis] Found '{target}' in registry -> {reg_url}")
                return self.install(reg_url, _visited=visited)
            return self._install_from_pypi(target, _visited=visited)

        self._save_manifest(manifest)

        visited.add(name.lower())
        for dep in deps:
            if dep.lower() not in visited and not self._resolve_package_key(packages, dep):
                self._log(f"[jarvis] Installing dependency '{dep}' for '{name}'...")
                self.install(dep, _visited=visited)

        return name, packages[name]

    def rebuild_all_bridges(self):
        manifest = self._load_manifest()
        packages = manifest.get("packages", {})
        rebuilt = []
        visited = set()

        for pkg_name in list(packages.keys()):
            info = packages[pkg_name]
            if info.get("type") in ("pypi", "plain-pypi", "plain-pypi-dep"):
                self._sync_pypi_dependency_tree(pkg_name, manifest, visited)
            else:
                version = info.get("version", "0.1.0")
                src_path = info.get("package_dir") or info.get("source")
                plain_path = self._generate_plain_bridge(pkg_name, version, source_path=src_path)
                info["path"] = plain_path

        self._save_manifest(manifest)
        for pkg_name, info in manifest.get("packages", {}).items():
            rebuilt.append((pkg_name, info.get("path")))
        return rebuilt

    def list_packages(self):
        return self._load_manifest().get("packages", {})

    def _collect_removable_dependencies(self, packages, root_key):
        to_remove_set = {root_key.lower()}
        queue = list(packages.get(root_key, {}).get("dependencies", []))
        candidates = []

        while queue:
            dep_name = queue.pop(0)
            matched_dep = self._resolve_package_key(packages, dep_name)
            if not matched_dep:
                continue
            low_dep = matched_dep.lower()
            if low_dep in to_remove_set:
                continue

            # Check if any other package outside the removal set still lists this dependency
            still_needed = False
            for other_key, other_info in packages.items():
                if other_key.lower() in to_remove_set:
                    continue
                for other_dep in other_info.get("dependencies", []):
                    if other_dep.lower() == low_dep:
                        still_needed = True
                        break
                if still_needed:
                    break

            if not still_needed:
                to_remove_set.add(low_dep)
                candidates.append(matched_dep)
                for nested_dep in packages.get(matched_dep, {}).get("dependencies", []):
                    queue.append(nested_dep)

        return candidates

    def _delete_single_package(self, packages, pkg_key):
        self._log(f"[jarvis] Removing '{pkg_key}'...")
        pkg_info = packages[pkg_key]

        pkg_path = pkg_info.get("path")
        if pkg_path and os.path.exists(pkg_path):
            if os.path.isdir(pkg_path):
                shutil.rmtree(pkg_path)
            else:
                os.remove(pkg_path)

        extra_dir = pkg_info.get("package_dir")
        if extra_dir and os.path.exists(extra_dir):
            if os.path.isdir(extra_dir):
                shutil.rmtree(extra_dir)
            else:
                os.remove(extra_dir)

        if pkg_info.get("type") in ("pypi", "plain-pypi", "plain-pypi-dep"):
            self._log(f"[jarvis] Uninstalling '{pkg_key}' via pip...")
            subprocess.run(
                [self.python_exe, "-m", "pip", "uninstall", "-y", pkg_key],
                capture_output=True,
                text=True,
                check=False,
            )
        del packages[pkg_key]

    def remove(self, pkg_name, deps_mode="ask"):
        manifest = self._load_manifest()
        packages = manifest.get("packages", {})
        matched_key = self._resolve_package_key(packages, pkg_name)
        if not matched_key:
            return []

        dep_candidates = self._collect_removable_dependencies(packages, matched_key)
        removed_keys = [matched_key]
        self._delete_single_package(packages, matched_key)

        if dep_candidates and deps_mode != "none":
            should_remove_deps = False
            if deps_mode == "yes":
                should_remove_deps = True
            else:
                print(f"[jarvis] '{matched_key}' has unused dependencies: {', '.join(dep_candidates)}")
                try:
                    ans = input("Remove these dependencies too? [y/N]: ").strip().lower()
                except EOFError:
                    ans = ""
                should_remove_deps = ans in ("y", "yes")

            if should_remove_deps:
                for dep_key in dep_candidates:
                    if dep_key in packages:
                        removed_keys.append(dep_key)
                        self._delete_single_package(packages, dep_key)

        self._save_manifest(manifest)
        return removed_keys


def jarvis_main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] in ("-h", "--help", "help"):
        print("jarvis — PlainLang Package Installer")
        print("Usage:")
        print("  jarvis init                                        Create .pvenv and activation scripts")
        print("  jarvis install <path|url|package_name>             Install a PlainLang or PyPI package with dependencies")
        print("  jarvis rebuild                                     Regenerate .plain files for all packages & dependencies")
        print("  jarvis list                                        List installed packages")
        print("  jarvis remove <package_name> [--with-deps|--no-deps]  Uninstall a package (prompts for dependencies)")
        print("  jarvis info <package_name>                         Show details for an installed package")
        return

    cmd = args[0].lower()

    try:
        if cmd == "init":
            print("[jarvis] Initializing .pvenv environment...", flush=True)
            env_dir = ensure_pvenv()
            print(f"Initialized .pvenv at {env_dir}")
            print(f"Activate in PowerShell: & \"{os.path.join(env_dir, 'Scripts', 'Activate.ps1')}\"")
            return

        installer = JarvisInstaller(verbose=True)
        if cmd == "install" and len(args) > 1:
            name, info = installer.install(args[1])
            print(f"Installed '{name}' (v{info['version']}) [{info['type']}] -> {info['path']}")
        elif cmd == "rebuild":
            rebuilt = installer.rebuild_all_bridges()
            print(f"Rebuilt {len(rebuilt)} .plain package file(s) including dependencies.")
        elif cmd == "list":
            pkgs = installer.list_packages()
            if not pkgs:
                print(f"No packages installed in {installer.env_path}")
                return
            for name, info in pkgs.items():
                print(f"- {name} (v{info.get('version', '0.1.0')}) [{info.get('type')}] -> {info.get('path')}")
        elif cmd == "remove" and len(args) > 1:
            pkg_target = args[1]
            flags = {a.lower() for a in args[2:]}
            if "--with-deps" in flags:
                deps_mode = "yes"
            elif "--no-deps" in flags:
                deps_mode = "no"
            else:
                deps_mode = "ask"

            removed = installer.remove(pkg_target, deps_mode=deps_mode)
            if not removed:
                print(f"Package '{pkg_target}' is not installed.")
            else:
                print(f"Removed: {', '.join(removed)}")
        elif cmd == "info" and len(args) > 1:
            pkgs = installer.list_packages()
            matched_key = installer._resolve_package_key(pkgs, args[1])
            if not matched_key:
                print(f"Package '{args[1]}' is not installed.")
                return
            print(json.dumps(pkgs[matched_key], indent=2))
        else:
            print("Invalid jarvis command. Run 'jarvis help' for usage.")
    except JarvisError as exc:
        print(f"jarvis error: {exc}")
        sys.exit(1)