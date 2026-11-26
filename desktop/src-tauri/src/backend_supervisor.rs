use serde::Serialize;
use std::env;
use std::fs::OpenOptions;
use std::io::{Read, Write};
use std::net::{SocketAddr, TcpStream};
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;
use std::time::{Duration, Instant};

#[cfg(target_os = "windows")]
use std::os::windows::process::CommandExt;

#[cfg(target_os = "windows")]
const CREATE_NO_WINDOW: u32 = 0x0800_0000;

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct BackendSupervisorStatus {
    pub managed: bool,
    pub running: bool,
    pub port_open: bool,
    pub pid: Option<u32>,
    pub mode: String,
    pub repo_root: Option<String>,
    pub last_error: Option<String>,
}

struct InnerState {
    child: Option<Child>,
    mode: String,
    repo_root: Option<PathBuf>,
    port: u16,
    last_error: Option<String>,
}

pub struct BackendSupervisor {
    inner: Mutex<InnerState>,
}

impl BackendSupervisor {
    pub fn new() -> Self {
        let repo_root = resolve_repo_root();
        let port = resolve_backend_port(repo_root.as_deref());
        Self {
            inner: Mutex::new(InnerState {
                child: None,
                mode: "idle".to_string(),
                repo_root,
                port,
                last_error: None,
            }),
        }
    }

    pub fn ensure_started(&self) -> BackendSupervisorStatus {
        let mut st = self.inner.lock().unwrap();

        if let Some(child) = st.child.as_mut() {
            match child.try_wait() {
                Ok(None) => return snapshot(&st),
                _ => {
                    st.child = None;
                }
            }
        }

        // 若外部已手动启动后端端口，直接复用避免端口冲突
        if is_backend_port_open(st.port) {
            st.mode = "external".to_string();
            st.last_error = None;
            return snapshot(&st);
        }

        let bundled_luajit = find_bundled_luajit(st.repo_root.as_deref());

        // 1. 优先启动打包内嵌的免 Python 独立后端（Release 安装包模式）
        if let Some(sidecar_exe) = find_bundled_backend_exe() {
            match spawn_bundled_backend(&sidecar_exe, bundled_luajit.as_deref()) {
                Ok(child) => {
                    st.child = Some(child);
                    st.mode = "bundled".to_string();
                    st.last_error = None;
                    return snapshot(&st);
                }
                Err(err) => {
                    st.last_error = Some(err);
                }
            }
        }

        // 2. 开发 / 源码运行模式：回落到本机 Python 启动 backend.api.server
        let Some(root) = st.repo_root.clone().or_else(resolve_repo_root) else {
            if st.last_error.is_none() {
                st.last_error = Some(
                    "未找到内置后端程序，也未定位到源码仓库目录（可设置 POE2_WORKBENCH_ROOT）".to_string(),
                );
            }
            return snapshot(&st);
        };
        st.repo_root = Some(root.clone());
        st.port = resolve_backend_port(Some(&root));

        match spawn_python_backend(&root, bundled_luajit.as_deref()) {
            Ok(child) => {
                st.child = Some(child);
                st.mode = "python".to_string();
                st.last_error = None;
            }
            Err(err) => {
                st.last_error = Some(err);
            }
        }
        snapshot(&st)
    }

    pub fn restart(&self) -> BackendSupervisorStatus {
        self.stop();
        self.ensure_started()
    }

    pub fn stop(&self) {
        let mut st = self.inner.lock().unwrap();
        let port = st.port;
        if let Some(mut child) = st.child.take() {
            // 优先通过本机 POST /shutdown（全平台通用）或 SIGTERM（Unix）优雅停机，
            // 让 Python 后端执行 rt.shutdown() 回收常驻 LuaJIT Worker，避免残留 450~550MB 孤儿进程。
            let mut signaled = try_http_shutdown(port);

            #[cfg(not(target_os = "windows"))]
            if !signaled {
                let pid = child.id().to_string();
                if Command::new("kill")
                    .args(["-TERM", &pid])
                    .stdin(Stdio::null())
                    .stdout(Stdio::null())
                    .stderr(Stdio::null())
                    .status()
                    .map(|s| s.success())
                    .unwrap_or(false)
                {
                    signaled = true;
                }
            }

            let mut exited = false;
            if signaled {
                let deadline = Instant::now() + Duration::from_millis(2000);
                while Instant::now() < deadline {
                    match child.try_wait() {
                        Ok(Some(_)) => {
                            exited = true;
                            break;
                        }
                        Ok(None) => std::thread::sleep(Duration::from_millis(50)),
                        Err(_) => break,
                    }
                }
            }

            if !exited {
                let _ = child.kill();
                let _ = child.wait();
            }
        }
    }

    pub fn status(&self) -> BackendSupervisorStatus {
        let mut st = self.inner.lock().unwrap();
        if let Some(child) = st.child.as_mut() {
            if let Ok(Some(exit_status)) = child.try_wait() {
                st.child = None;
                if !exit_status.success() {
                    let tail = read_backend_log_tail();
                    st.last_error = Some(if tail.is_empty() {
                        format!("本地后端异常退出 ({exit_status})")
                    } else {
                        format!("本地后端异常退出 ({exit_status})：{tail}")
                    });
                }
            }
        }
        snapshot(&st)
    }
}

fn snapshot(st: &InnerState) -> BackendSupervisorStatus {
    let pid = st.child.as_ref().map(|c| c.id());
    let port_open = is_backend_port_open(st.port);
    BackendSupervisorStatus {
        managed: pid.is_some(),
        running: pid.is_some() || port_open,
        port_open,
        pid,
        mode: st.mode.clone(),
        repo_root: st.repo_root.as_ref().map(|p| p.display().to_string()),
        last_error: st.last_error.clone(),
    }
}

fn resolve_backend_port(repo_root: Option<&Path>) -> u16 {
    if let Ok(raw) = env::var("POE2_PORT") {
        if let Ok(p) = raw.trim().parse::<u16>() {
            if p > 0 {
                return p;
            }
        }
    }
    if let Some(root) = repo_root {
        if let Ok(text) = std::fs::read_to_string(root.join(".env.local")) {
            for line in text.lines() {
                let trimmed = line.trim();
                if trimmed.is_empty() || trimmed.starts_with('#') {
                    continue;
                }
                if let Some((k, v)) = trimmed.split_once('=') {
                    if k.trim() == "POE2_PORT" {
                        let cleaned = v.trim().trim_matches('"').trim_matches('\'');
                        if let Ok(p) = cleaned.parse::<u16>() {
                            if p > 0 {
                                return p;
                            }
                        }
                    }
                }
            }
        }
    }
    8788
}

fn is_backend_port_open(port: u16) -> bool {
    let addr = SocketAddr::from(([127, 0, 0, 1], port));
    TcpStream::connect_timeout(&addr, Duration::from_millis(250)).is_ok()
}

fn workbench_state_dir() -> Option<PathBuf> {
    let home = env::var("HOME")
        .or_else(|_| env::var("USERPROFILE"))
        .ok()?;
    Some(PathBuf::from(home).join(".poe2-workbench"))
}

fn backend_log_path() -> Option<PathBuf> {
    workbench_state_dir().map(|d| d.join("backend.log"))
}

fn read_backend_log_tail() -> String {
    let Some(path) = backend_log_path() else {
        return String::new();
    };
    let Ok(raw) = std::fs::read_to_string(path) else {
        return String::new();
    };
    let trimmed = raw.trim();
    if trimmed.is_empty() {
        return String::new();
    }
    let lines: Vec<&str> = trimmed.lines().rev().take(4).collect();
    lines.into_iter().rev().collect::<Vec<&str>>().join(" | ")
}

fn read_auth_token() -> Option<String> {
    if let Ok(tok) = env::var("POE2_TOKEN") {
        let t = tok.trim().to_string();
        if !t.is_empty() {
            return Some(t);
        }
    }
    let token_path = workbench_state_dir()?.join("token");
    let raw = std::fs::read_to_string(token_path).ok()?;
    let t = raw.trim().to_string();
    if t.is_empty() { None } else { Some(t) }
}

fn try_http_shutdown(port: u16) -> bool {
    let Some(token) = read_auth_token() else {
        return false;
    };
    let addr = SocketAddr::from(([127, 0, 0, 1], port));
    let Ok(mut stream) = TcpStream::connect_timeout(&addr, Duration::from_millis(300)) else {
        return false;
    };
    let _ = stream.set_read_timeout(Some(Duration::from_millis(500)));
    let _ = stream.set_write_timeout(Some(Duration::from_millis(500)));
    let req = format!(
        "POST /shutdown HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nX-Auth-Token: {token}\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"
    );
    if stream.write_all(req.as_bytes()).is_err() {
        return false;
    }
    let mut buf = [0u8; 128];
    match stream.read(&mut buf) {
        Ok(n) if n > 0 => {
            let head = String::from_utf8_lossy(&buf[..n]);
            head.starts_with("HTTP/1.0 200") || head.starts_with("HTTP/1.1 200")
        }
        _ => false,
    }
}

fn has_backend_server(dir: &Path) -> bool {
    dir.join("backend").join("api").join("server.py").is_file()
}

fn resolve_repo_root() -> Option<PathBuf> {
    if let Ok(configured) = env::var("POE2_WORKBENCH_ROOT") {
        let p = PathBuf::from(configured);
        if has_backend_server(&p) {
            return Some(p);
        }
    }

    if let Ok(cwd) = env::current_dir() {
        for anc in cwd.ancestors() {
            if has_backend_server(anc) {
                return Some(anc.to_path_buf());
            }
        }
    }

    if let Ok(exe) = env::current_exe() {
        for anc in exe.ancestors() {
            if has_backend_server(anc) {
                return Some(anc.to_path_buf());
            }
        }
    }

    // 编译期兜底：打包后移至 /Applications 等独立目录时仍能定位本机仓库
    let manifest_dir = Path::new(env!("CARGO_MANIFEST_DIR"));
    if let Some(repo) = manifest_dir.parent().and_then(|p| p.parent()) {
        if has_backend_server(repo) {
            return Some(repo.to_path_buf());
        }
    }

    None
}

fn python_candidates() -> &'static [&'static str] {
    #[cfg(target_os = "windows")]
    {
        &["python", "py", "python3"]
    }
    #[cfg(not(target_os = "windows"))]
    {
        &[
            "/opt/homebrew/bin/python3",
            "/usr/local/bin/python3",
            "/usr/bin/python3",
            "python3",
            "python",
        ]
    }
}

fn open_backend_stderr_stdio() -> Stdio {
    if let Some(dir) = workbench_state_dir() {
        let _ = std::fs::create_dir_all(&dir);
        let log_path = dir.join("backend.log");
        if let Ok(file) = OpenOptions::new()
            .create(true)
            .write(true)
            .truncate(true)
            .open(log_path)
        {
            return Stdio::from(file);
        }
    }
    Stdio::null()
}

fn backend_exe_name() -> &'static str {
    #[cfg(target_os = "windows")]
    {
        "poe2-workbench-backend.exe"
    }
    #[cfg(not(target_os = "windows"))]
    {
        "poe2-workbench-backend"
    }
}

fn luajit_exe_name() -> &'static str {
    #[cfg(target_os = "windows")]
    {
        "luajit.exe"
    }
    #[cfg(not(target_os = "windows"))]
    {
        "luajit"
    }
}

fn find_bundled_backend_exe() -> Option<PathBuf> {
    let name = backend_exe_name();
    let exe = env::current_exe().ok()?;
    let exe_dir = exe.parent()?;
    let mut candidates: Vec<PathBuf> = vec![
        exe_dir.join("resources").join("backend").join(name),
        exe_dir.join("backend").join(name),
        exe_dir.join(name),
    ];
    if let Some(parent) = exe_dir.parent() {
        // macOS .app/Contents/MacOS/<app> -> .app/Contents/Resources/...
        candidates.push(parent.join("Resources").join("resources").join("backend").join(name));
        candidates.push(parent.join("Resources").join("backend").join(name));
        // Linux AppImage /usr/bin -> /usr/lib/<pkg>/resources/backend/...
        candidates.push(parent.join("lib").join("poe2-workbench").join("resources").join("backend").join(name));
    }
    candidates.into_iter().find(|p| p.is_file())
}

fn find_bundled_luajit(repo_root: Option<&Path>) -> Option<PathBuf> {
    let name = luajit_exe_name();
    let mut candidates: Vec<PathBuf> = Vec::new();
    if let Ok(exe) = env::current_exe() {
        if let Some(exe_dir) = exe.parent() {
            candidates.push(exe_dir.join("resources").join("bin").join(name));
            candidates.push(exe_dir.join("bin").join(name));
            candidates.push(exe_dir.join(name));
            if let Some(parent) = exe_dir.parent() {
                candidates.push(parent.join("Resources").join("resources").join("bin").join(name));
                candidates.push(parent.join("Resources").join("bin").join(name));
            }
        }
    }
    if let Some(root) = repo_root {
        candidates.push(root.join("desktop").join("src-tauri").join("resources").join("bin").join(name));
        candidates.push(root.join("desktop").join("src-tauri").join("binaries").join(name));
    }
    candidates.into_iter().find(|p| p.is_file())
}

fn apply_common_env(cmd: &mut Command, bundled_luajit: Option<&Path>) {
    if let Some(lj) = bundled_luajit {
        if env::var("POB2_LUAJIT").map(|v| v.trim().is_empty()).unwrap_or(true) {
            cmd.env("POB2_LUAJIT", lj);
        }
    }
    #[cfg(not(target_os = "windows"))]
    {
        let cur_path = env::var("PATH").unwrap_or_default();
        let extra = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin";
        let merged = if cur_path.is_empty() {
            extra.to_string()
        } else {
            format!("{extra}:{cur_path}")
        };
        cmd.env("PATH", merged);
    }

    #[cfg(target_os = "windows")]
    cmd.creation_flags(CREATE_NO_WINDOW);
}

fn spawn_bundled_backend(exe_path: &Path, bundled_luajit: Option<&Path>) -> Result<Child, String> {
    let mut cmd = Command::new(exe_path);
    if let Some(parent) = exe_path.parent() {
        cmd.current_dir(parent);
    }
    cmd.stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(open_backend_stderr_stdio());
    apply_common_env(&mut cmd, bundled_luajit);
    cmd.spawn()
        .map_err(|e| format!("无法启动内置后端 ({}): {e}", exe_path.display()))
}

fn spawn_python_backend(repo_root: &Path, bundled_luajit: Option<&Path>) -> Result<Child, String> {
    let mut last_err = String::new();
    for bin in python_candidates() {
        let mut cmd = Command::new(bin);
        cmd.arg("-m")
            .arg("backend.api.server")
            .current_dir(repo_root)
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(open_backend_stderr_stdio());

        apply_common_env(&mut cmd, bundled_luajit);

        match cmd.spawn() {
            Ok(child) => return Ok(child),
            Err(e) => {
                last_err = format!("{bin}: {e}");
            }
        }
    }
    Err(format!("无法拉起本地 Python 后端：{last_err}"))
}
