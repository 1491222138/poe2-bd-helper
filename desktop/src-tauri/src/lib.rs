mod backend_supervisor;

use backend_supervisor::{BackendSupervisor, BackendSupervisorStatus};
use std::sync::Mutex;
use tauri::menu::{Menu, MenuItem};
use tauri::tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent};
use tauri::{AppHandle, Emitter, Manager, RunEvent, State, WindowEvent};
use tauri_plugin_deep_link::DeepLinkExt;

struct DeepLinkQueue(Mutex<Vec<String>>);

fn show_and_focus_main(app: &AppHandle) {
    if let Some(win) = app.get_webview_window("main") {
        let _ = win.show();
        let _ = win.unminimize();
        let _ = win.set_focus();
    }
}

fn dispatch_deep_link(app: &AppHandle, url: String) {
    if !url.to_lowercase().starts_with("poe2wb://") {
        return;
    }
    if let Some(q) = app.try_state::<DeepLinkQueue>() {
        if let Ok(mut guard) = q.0.lock() {
            guard.push(url.clone());
        }
    }
    show_and_focus_main(app);
    let _ = app.emit("poe2wb://deep-link", url);
}

#[tauri::command]
fn backend_status(sup: State<'_, BackendSupervisor>) -> BackendSupervisorStatus {
    sup.status()
}

#[tauri::command]
fn restart_backend(sup: State<'_, BackendSupervisor>) -> BackendSupervisorStatus {
    sup.restart()
}

#[tauri::command]
fn pending_deep_links(queue: State<'_, DeepLinkQueue>) -> Vec<String> {
    let mut guard = queue.0.lock().unwrap();
    let drained = guard.clone();
    guard.clear();
    drained
}

#[tauri::command]
fn open_external_url(url: String) -> Result<(), String> {
    let trimmed = url.trim();
    if !(trimmed.starts_with("https://") || trimmed.starts_with("http://")) {
        return Err("仅允许打开 http/https 外部链接".to_string());
    }
    #[cfg(target_os = "macos")]
    let status = std::process::Command::new("open").arg(trimmed).status();
    #[cfg(target_os = "windows")]
    let status = std::process::Command::new("rundll32")
        .args(["url.dll,FileProtocolHandler", trimmed])
        .status();
    #[cfg(all(unix, not(target_os = "macos")))]
    let status = std::process::Command::new("xdg-open").arg(trimmed).status();

    match status {
        Ok(s) if s.success() => Ok(()),
        Ok(s) => Err(format!("打开系统浏览器失败 (exit={:?})", s.code())),
        Err(e) => Err(format!("拉起系统浏览器失败: {}", e)),
    }
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let app = tauri::Builder::default()
        .manage(BackendSupervisor::new())
        .manage(DeepLinkQueue(Mutex::new(Vec::new())))
        .plugin(tauri_plugin_single_instance::init(|app, argv, _cwd| {
            show_and_focus_main(app);
            for arg in argv {
                if arg.to_lowercase().starts_with("poe2wb://") {
                    dispatch_deep_link(app, arg);
                }
            }
        }))
        .plugin(tauri_plugin_deep_link::init())
        .invoke_handler(tauri::generate_handler![
            backend_status,
            restart_backend,
            pending_deep_links,
            open_external_url
        ])
        .setup(|app| {
            // 1. 启动/接管本地 127.0.0.1:8788 统一引擎后端
            let sup = app.state::<BackendSupervisor>();
            let _ = sup.ensure_started();

            // 2. 监听 poe2wb:// Deep Link 唤醒（扩展离线兜底一键拉起桌面端）
            let handle_for_dl = app.handle().clone();
            app.deep_link().on_open_url(move |event| {
                for url in event.urls() {
                    dispatch_deep_link(&handle_for_dl, url.to_string());
                }
            });

            // 3. 系统托盘：支持常驻后台保持 LuaJIT Worker 热态
            let show_item = MenuItem::with_id(app, "show", "打开 PoE2 Workbench", true, None::<&str>)?;
            let restart_item = MenuItem::with_id(app, "restart", "重启本地引擎后端", true, None::<&str>)?;
            let quit_item = MenuItem::with_id(app, "quit", "退出（关闭后端引擎）", true, None::<&str>)?;
            let menu = Menu::with_items(app, &[&show_item, &restart_item, &quit_item])?;

            let mut tray_builder = TrayIconBuilder::new()
                .menu(&menu)
                .tooltip("PoE2 Workbench (127.0.0.1:8788)")
                .on_menu_event(|app, event| match event.id.as_ref() {
                    "show" => show_and_focus_main(app),
                    "restart" => {
                        let sup = app.state::<BackendSupervisor>();
                        let st = sup.restart();
                        let _ = app.emit("poe2wb://backend-status", st);
                    }
                    "quit" => {
                        let sup = app.state::<BackendSupervisor>();
                        sup.stop();
                        app.exit(0);
                    }
                    _ => {}
                })
                .on_tray_icon_event(|tray, event| {
                    if let TrayIconEvent::Click {
                        button: MouseButton::Left,
                        button_state: MouseButtonState::Up,
                        ..
                    } = event
                    {
                        show_and_focus_main(tray.app_handle());
                    }
                });

            if let Some(icon) = app.default_window_icon().cloned() {
                tray_builder = tray_builder.icon(icon);
            }
            let _tray = tray_builder.build(app)?;

            Ok(())
        })
        .on_window_event(|window, event| {
            // 关闭主窗口时最小化到托盘，保持常驻 LuaJIT Worker 与 SSE 通道不断线
            if let WindowEvent::CloseRequested { api, .. } = event {
                let _ = window.hide();
                api.prevent_close();
            }
        })
        .build(tauri::generate_context!())
        .expect("error while building PoE2 Workbench Tauri application");

    app.run(|app_handle, event| {
        if let RunEvent::Exit = event {
            let sup = app_handle.state::<BackendSupervisor>();
            sup.stop();
        }
    });
}
