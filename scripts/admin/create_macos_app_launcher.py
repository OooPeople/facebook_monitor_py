"""Admin tool：為 macOS onedir build 建立 Finder/Dock 用 .app 外殼。"""

# ruff: noqa: E402

from __future__ import annotations

import argparse
import plistlib
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from facebook_monitor.version import APP_VERSION
from facebook_monitor.updates.platforms import MACOS_APP_ENTRY
from facebook_monitor.updates.platforms import MACOS_APP_BUNDLE_LAUNCHER
from facebook_monitor.updates.platforms import MACOS_APP_BUNDLE_LAUNCHER_ENV
from facebook_monitor.updates.platforms import MACOS_APP_BUNDLE_LAUNCHER_ENV_VALUE
from facebook_monitor.updates.platforms import MACOS_APP_BUNDLE_NAME


APP_ROOT_NAME = MACOS_APP_ENTRY
BUNDLE_NAME = MACOS_APP_BUNDLE_NAME
BUNDLE_DISPLAY_NAME = "Facebook Monitor"
BUNDLE_IDENTIFIER = "com.ooopeople.facebook-monitor"
LAUNCHER_EXECUTABLE_NAME = Path(MACOS_APP_BUNDLE_LAUNCHER).name
ICON_BASENAME = "facebook-monitor"
DEFAULT_ICON_SOURCE = ROOT / "packaging" / "assets" / "facebook-monitor.png"


def parse_args() -> argparse.Namespace:
    """解析 CLI 參數。"""

    parser = argparse.ArgumentParser(
        description="Create a macOS .app launcher beside/inside the frozen onedir app."
    )
    parser.add_argument(
        "--app-root",
        type=Path,
        default=ROOT / "dist" / APP_ROOT_NAME,
        help="Frozen onedir app root that contains the facebook-monitor executable.",
    )
    parser.add_argument(
        "--icon-source",
        type=Path,
        default=DEFAULT_ICON_SOURCE,
        help="PNG icon source used to generate the .app icns file.",
    )
    parser.add_argument(
        "--version",
        default=APP_VERSION,
        help="Bundle version. Defaults to facebook_monitor.version.APP_VERSION.",
    )
    return parser.parse_args()


def main() -> int:
    """CLI entrypoint。"""

    args = parse_args()
    bundle = create_macos_app_launcher(
        app_root=args.app_root.resolve(),
        icon_source=args.icon_source.resolve(),
        version=str(args.version),
    )
    print(bundle)
    return 0


def create_macos_app_launcher(
    *,
    app_root: Path,
    icon_source: Path = DEFAULT_ICON_SOURCE,
    version: str = APP_VERSION,
    convert_icon: bool = True,
    compile_launcher: bool = True,
) -> Path:
    """在 frozen onedir 內建立 `Facebook Monitor.app` launcher bundle。"""

    app_root = app_root.resolve()
    app_entry = app_root / APP_ROOT_NAME
    if not app_entry.is_file():
        raise ValueError(f"missing macOS app executable: {app_entry}")
    bundle_dir = app_root / BUNDLE_NAME
    if bundle_dir.exists():
        shutil.rmtree(bundle_dir)

    contents_dir = bundle_dir / "Contents"
    macos_dir = contents_dir / "MacOS"
    resources_dir = contents_dir / "Resources"
    macos_dir.mkdir(parents=True)
    resources_dir.mkdir(parents=True)

    launcher_path = macos_dir / LAUNCHER_EXECUTABLE_NAME
    if compile_launcher:
        _compile_native_launcher(launcher_path)
    else:
        launcher_path.write_text(_native_launcher_source(), encoding="utf-8")
        launcher_path.chmod(0o755)
    (contents_dir / "Info.plist").write_bytes(
        plistlib.dumps(_info_plist(version=version), sort_keys=True)
    )
    if convert_icon:
        _create_icns(icon_source, resources_dir / f"{ICON_BASENAME}.icns")
    elif icon_source.is_file():
        shutil.copy2(icon_source, resources_dir / icon_source.name)
    return bundle_dir


def _compile_native_launcher(destination: Path) -> None:
    """編譯 Dock 常駐 launcher，讓 `.app` 保持為可關閉的母程序。"""

    clang = _find_clang()
    sdk_path = _find_macos_sdk_path()
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="facebook-monitor-launcher-") as temp_dir:
        source = Path(temp_dir) / "facebook-monitor-launcher.m"
        source.write_text(_native_launcher_source(), encoding="utf-8")
        command = [
            clang,
            "-fobjc-arc",
            "-arch",
            "arm64",
        ]
        if sdk_path:
            command.extend(["-isysroot", sdk_path])
        command.extend(
            [
                "-framework",
                "Cocoa",
                str(source),
                "-o",
                str(destination),
            ]
        )
        subprocess.run(command, check=True)
    destination.chmod(0o755)


def _find_clang() -> str:
    """尋找 macOS native launcher 所需的 Objective-C compiler。"""

    xcrun = shutil.which("xcrun")
    if xcrun:
        try:
            detected = subprocess.check_output(
                [xcrun, "--sdk", "macosx", "--find", "clang"],
                text=True,
                stderr=subprocess.DEVNULL,
            ).strip()
        except subprocess.SubprocessError:
            detected = ""
        if detected:
            return detected
    clang = shutil.which("clang")
    if clang:
        return clang
    raise ValueError("macOS native launcher compiler not found: clang")


def _find_macos_sdk_path() -> str:
    """透過 xcrun 取得 macOS SDK path；失敗時讓 clang 使用自己的預設值。"""

    xcrun = shutil.which("xcrun")
    if not xcrun:
        return ""
    try:
        return subprocess.check_output(
            [xcrun, "--sdk", "macosx", "--show-sdk-path"],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except subprocess.SubprocessError:
        return ""


def _native_launcher_source() -> str:
    """回傳 `.app` 內部 native launcher 的 Objective-C source。"""

    source = r"""#import <Cocoa/Cocoa.h>
#include <signal.h>

static dispatch_source_t gTermSource;
static dispatch_source_t gIntSource;
static dispatch_source_t gHupSource;

static dispatch_source_t InstallTerminationSignalHandler(int signalNumber) {
    signal(signalNumber, SIG_IGN);
    dispatch_source_t source = dispatch_source_create(DISPATCH_SOURCE_TYPE_SIGNAL, signalNumber, 0, dispatch_get_main_queue());
    dispatch_source_set_event_handler(source, ^{
        [NSApp terminate:nil];
    });
    dispatch_resume(source);
    return source;
}

@interface AppDelegate : NSObject <NSApplicationDelegate>
@property(nonatomic, strong) NSTask *task;
@end

@implementation AppDelegate

- (void)applicationDidFinishLaunching:(NSNotification *)notification {
    (void)notification;
    NSString *bundlePath = [[NSBundle mainBundle] bundlePath];
    NSString *appRoot = [bundlePath stringByDeletingLastPathComponent];
    NSString *executable = [appRoot stringByAppendingPathComponent:@"__FACEBOOK_MONITOR_APP_ENTRY__"];

    if (![[NSFileManager defaultManager] isExecutableFileAtPath:executable]) {
        NSAlert *alert = [[NSAlert alloc] init];
        [alert setMessageText:@"Facebook Monitor"];
        [alert setInformativeText:@"找不到 __FACEBOOK_MONITOR_APP_ENTRY__ executable。請確認 Facebook Monitor.app 仍在 facebook-monitor 資料夾內。"];
        [alert setAlertStyle:NSAlertStyleCritical];
        [alert runModal];
        [NSApp terminate:nil];
        return;
    }

    NSArray<NSString *> *processArgs = [[NSProcessInfo processInfo] arguments];
    NSMutableArray<NSString *> *childArgs = [NSMutableArray array];
    if ([processArgs count] > 1) {
        for (NSString *arg in [processArgs subarrayWithRange:NSMakeRange(1, [processArgs count] - 1)]) {
            if ([arg hasPrefix:@"-psn_"]) {
                continue;
            }
            [childArgs addObject:arg];
        }
    }

    NSMutableDictionary<NSString *, NSString *> *environment = [[[NSProcessInfo processInfo] environment] mutableCopy];
    [environment setObject:@"__FACEBOOK_MONITOR_LAUNCHER_ENV_VALUE__"
                    forKey:@"__FACEBOOK_MONITOR_LAUNCHER_ENV_KEY__"];

    self.task = [[NSTask alloc] init];
    [self.task setExecutableURL:[NSURL fileURLWithPath:executable]];
    [self.task setArguments:childArgs];
    [self.task setEnvironment:environment];
    [self.task setTerminationHandler:^(NSTask *finishedTask) {
        (void)finishedTask;
        dispatch_async(dispatch_get_main_queue(), ^{
            [NSApp terminate:nil];
        });
    }];

    NSError *error = nil;
    if (![self.task launchAndReturnError:&error]) {
        NSAlert *alert = [[NSAlert alloc] init];
        [alert setMessageText:@"Facebook Monitor"];
        [alert setInformativeText:[NSString stringWithFormat:@"無法啟動 __FACEBOOK_MONITOR_APP_ENTRY__：%@", [error localizedDescription]]];
        [alert setAlertStyle:NSAlertStyleCritical];
        [alert runModal];
        [NSApp terminate:nil];
        return;
    }
}

- (NSApplicationTerminateReply)applicationShouldTerminate:(NSApplication *)sender {
    (void)sender;
    if (self.task != nil && [self.task isRunning]) {
        [self.task terminate];
        NSDate *deadline = [NSDate dateWithTimeIntervalSinceNow:5.0];
        while ([self.task isRunning] && [deadline timeIntervalSinceNow] > 0) {
            [[NSRunLoop currentRunLoop] runMode:NSDefaultRunLoopMode
                                     beforeDate:[NSDate dateWithTimeIntervalSinceNow:0.05]];
        }
        if ([self.task isRunning]) {
            kill([self.task processIdentifier], SIGKILL);
        }
    }
    return NSTerminateNow;
}

@end

int main(int argc, const char * argv[]) {
    (void)argc;
    (void)argv;
    @autoreleasepool {
        NSApplication *app = [NSApplication sharedApplication];
        [app setActivationPolicy:NSApplicationActivationPolicyRegular];
        gTermSource = InstallTerminationSignalHandler(SIGTERM);
        gIntSource = InstallTerminationSignalHandler(SIGINT);
        gHupSource = InstallTerminationSignalHandler(SIGHUP);
        AppDelegate *delegate = [[AppDelegate alloc] init];
        [app setDelegate:delegate];
        [app run];
    }
    return 0;
}
"""
    return (
        source.replace(
            "__FACEBOOK_MONITOR_LAUNCHER_ENV_KEY__",
            MACOS_APP_BUNDLE_LAUNCHER_ENV,
        )
        .replace(
            "__FACEBOOK_MONITOR_LAUNCHER_ENV_VALUE__",
            MACOS_APP_BUNDLE_LAUNCHER_ENV_VALUE,
        )
        .replace("__FACEBOOK_MONITOR_APP_ENTRY__", MACOS_APP_ENTRY)
    )


def _info_plist(*, version: str) -> dict[str, object]:
    """建立 macOS launcher bundle 的 Info.plist。"""

    return {
        "CFBundleDevelopmentRegion": "zh_TW",
        "CFBundleDisplayName": BUNDLE_DISPLAY_NAME,
        "CFBundleExecutable": LAUNCHER_EXECUTABLE_NAME,
        "CFBundleIconFile": ICON_BASENAME,
        "CFBundleIdentifier": BUNDLE_IDENTIFIER,
        "CFBundleName": BUNDLE_DISPLAY_NAME,
        "CFBundlePackageType": "APPL",
        "CFBundleShortVersionString": version,
        "CFBundleVersion": version,
        "LSApplicationCategoryType": "public.app-category.utilities",
        "NSHighResolutionCapable": True,
    }


def _create_icns(source_png: Path, destination: Path) -> None:
    """用 macOS 內建工具從 PNG 產生 .icns。"""

    if not source_png.is_file():
        raise ValueError(f"missing icon source: {source_png}")
    sips = shutil.which("sips")
    iconutil = shutil.which("iconutil")
    if not sips or not iconutil:
        raise ValueError("macOS icon tools not found: sips/iconutil")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="facebook-monitor-iconset-") as temp_dir:
        iconset = Path(temp_dir) / f"{ICON_BASENAME}.iconset"
        iconset.mkdir()
        for size in (16, 32, 128, 256, 512):
            _run_sips_resize(
                sips,
                source_png,
                iconset / f"icon_{size}x{size}.png",
                size,
            )
            _run_sips_resize(
                sips,
                source_png,
                iconset / f"icon_{size}x{size}@2x.png",
                size * 2,
            )
        subprocess.run(
            [iconutil, "-c", "icns", str(iconset), "-o", str(destination)],
            check=True,
        )


def _run_sips_resize(
    sips: str,
    source_png: Path,
    destination: Path,
    size: int,
) -> None:
    """呼叫 sips 產生指定尺寸 icon PNG。"""

    subprocess.run(
        [
            sips,
            "-z",
            str(size),
            str(size),
            str(source_png),
            "--out",
            str(destination),
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


if __name__ == "__main__":
    raise SystemExit(main())
