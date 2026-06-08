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
from facebook_monitor.updates.platforms import MACOS_APP_BUNDLE_IDENTIFIER
from facebook_monitor.updates.platforms import MACOS_APP_BUNDLE_LAUNCHER
from facebook_monitor.updates.platforms import MACOS_APP_BUNDLE_LAUNCHER_ENV
from facebook_monitor.updates.platforms import MACOS_APP_BUNDLE_LAUNCHER_ENV_VALUE
from facebook_monitor.updates.platforms import MACOS_APP_BUNDLE_NAME
from facebook_monitor.updates.platforms import MACOS_APP_BUNDLE_NOTIFICATION_SEND_FLAG
from facebook_monitor.updates.platforms import MACOS_APP_BUNDLE_NOTIFICATION_SOCKET_ENV


APP_ROOT_NAME = MACOS_APP_ENTRY
BUNDLE_NAME = MACOS_APP_BUNDLE_NAME
BUNDLE_DISPLAY_NAME = "Facebook Monitor"
BUNDLE_IDENTIFIER = MACOS_APP_BUNDLE_IDENTIFIER
LAUNCHER_EXECUTABLE_NAME = Path(MACOS_APP_BUNDLE_LAUNCHER).name
ICON_BASENAME = "facebook-monitor"
# 只用來移除舊版曾產生的獨立 notification helper bundle；正式通知身分是主 `.app`。
STALE_NOTIFICATION_HELPER_BUNDLE_NAME = "Facebook Monitor Notification Helper.app"
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
    ad_hoc_sign: bool = True,
) -> Path:
    """在 frozen onedir 內建立主 `.app` launcher bundle。"""

    app_root = app_root.resolve()
    app_entry = app_root / APP_ROOT_NAME
    if not app_entry.is_file():
        raise ValueError(f"missing macOS app executable: {app_entry}")
    bundle_dir = app_root / BUNDLE_NAME
    if bundle_dir.exists():
        shutil.rmtree(bundle_dir)
    stale_helper_dir = app_root / STALE_NOTIFICATION_HELPER_BUNDLE_NAME
    if stale_helper_dir.exists():
        shutil.rmtree(stale_helper_dir)

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
    if ad_hoc_sign:
        _ad_hoc_sign_bundle(bundle_dir)
    return bundle_dir


def _compile_native_launcher(destination: Path) -> None:
    """編譯 Dock 常駐 launcher 與 UserNotifications socket 母程序。"""

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
                "-framework",
                "UserNotifications",
                str(source),
                "-o",
                str(destination),
            ]
        )
        subprocess.run(command, check=True)
    destination.chmod(0o755)


def _ad_hoc_sign_bundle(bundle_dir: Path) -> None:
    """用 ad-hoc signature 固定 `.app` identity，供 macOS notifications 辨識。"""

    codesign = shutil.which("codesign")
    if not codesign:
        raise ValueError("macOS app bundle signer not found: codesign")
    subprocess.run(
        [
            codesign,
            "--force",
            "--deep",
            "--sign",
            "-",
            str(bundle_dir),
        ],
        check=True,
    )


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
    """回傳主 `.app` launcher 與 notification socket 的 Objective-C source。"""

    source = r"""#import <Cocoa/Cocoa.h>
#import <UserNotifications/UserNotifications.h>
#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <signal.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/time.h>
#include <sys/un.h>
#include <unistd.h>

static dispatch_source_t gTermSource;
static dispatch_source_t gIntSource;
static dispatch_source_t gHupSource;
static NSDictionary *gNotificationSendPayload;
static int gNotificationSendExitCode = 2;

static dispatch_source_t InstallTerminationSignalHandler(int signalNumber) {
    signal(signalNumber, SIG_IGN);
    dispatch_source_t source = dispatch_source_create(DISPATCH_SOURCE_TYPE_SIGNAL, signalNumber, 0, dispatch_get_main_queue());
    dispatch_source_set_event_handler(source, ^{
        [NSApp terminate:nil];
    });
    dispatch_resume(source);
    return source;
}

@interface NotificationDelegate : NSObject <UNUserNotificationCenterDelegate>
@end

@implementation NotificationDelegate

- (void)userNotificationCenter:(UNUserNotificationCenter *)center
       willPresentNotification:(UNNotification *)notification
         withCompletionHandler:(void (^)(UNNotificationPresentationOptions options))completionHandler {
    (void)center;
    (void)notification;
    UNNotificationPresentationOptions options = UNNotificationPresentationOptionSound;
    if (@available(macOS 11.0, *)) {
        options |= UNNotificationPresentationOptionBanner | UNNotificationPresentationOptionList;
    } else {
#pragma clang diagnostic push
#pragma clang diagnostic ignored "-Wdeprecated-declarations"
        options |= UNNotificationPresentationOptionAlert;
#pragma clang diagnostic pop
    }
    completionHandler(options);
}

@end

static NotificationDelegate *gNotificationDelegate;

static NSString *StringValue(NSDictionary *payload, NSString *key) {
    id value = [payload objectForKey:key];
    if ([value isKindOfClass:[NSString class]]) {
        return (NSString *)value;
    }
    return @"";
}

static NSDictionary *NotificationPayloadFromData(NSData *data) {
    if (data == nil || [data length] == 0) {
        return nil;
    }
    NSError *error = nil;
    id object = [NSJSONSerialization JSONObjectWithData:data options:0 error:&error];
    if (error != nil || ![object isKindOfClass:[NSDictionary class]]) {
        return nil;
    }
    return (NSDictionary *)object;
}

static NSDictionary *NotificationResultPayloadWithDetails(
    BOOL ok,
    NSString *message,
    NSString *backend,
    NSDictionary *details
) {
    NSMutableDictionary *payload = [@{
        @"ok": @(ok),
        @"message": message ?: @"",
        @"backend": backend ?: @""
    } mutableCopy];
    if (details != nil) {
        [payload addEntriesFromDictionary:details];
    }
    return payload;
}

static NSDictionary *NotificationResultPayload(BOOL ok, NSString *message, NSString *backend) {
    return NotificationResultPayloadWithDetails(ok, message, backend, nil);
}

static NSDictionary *NotificationErrorDetails(NSError *error) {
    if (error == nil) {
        return @{};
    }
    return @{
        @"error_domain": [error domain] ?: @"",
        @"error_code": @([error code])
    };
}

static NSData *NotificationResultData(NSDictionary *payload) {
    if (payload == nil) {
        payload = NotificationResultPayload(NO, @"desktop_failed:macos_native_failed", @"");
    }
    NSError *error = nil;
    NSData *data = [NSJSONSerialization dataWithJSONObject:payload options:0 error:&error];
    if (error != nil || data == nil) {
        return nil;
    }
    NSMutableData *mutableData = [data mutableCopy];
    [mutableData appendData:[@"\n" dataUsingEncoding:NSUTF8StringEncoding]];
    return mutableData;
}

static NSDictionary *ReadNotificationPayload(void) {
    NSData *data = [[NSFileHandle fileHandleWithStandardInput] readDataToEndOfFile];
    return NotificationPayloadFromData(data);
}

static void WriteNotificationResultPayload(NSDictionary *payload) {
    NSData *data = NotificationResultData(payload);
    if (data == nil) {
        return;
    }
    [[NSFileHandle fileHandleWithStandardOutput] writeData:data];
}

static void WriteNotificationResult(BOOL ok, NSString *message, NSString *backend) {
    WriteNotificationResultPayload(NotificationResultPayload(ok, message, backend));
}

static int NotificationResultExitCode(NSDictionary *payload) {
    id ok = [payload objectForKey:@"ok"];
    return ([ok respondsToSelector:@selector(boolValue)] && [ok boolValue]) ? 0 : 2;
}

static NSDictionary *DeliverNotificationPayload(NSDictionary *payload, NSString *backendName) {
    NSString *title = StringValue(payload, @"title");
    NSString *body = StringValue(payload, @"body");
    NSString *identifier = StringValue(payload, @"identifier");
    if ([title length] == 0) {
        title = @"Facebook Monitor";
    }
    if ([identifier length] == 0) {
        identifier = [[NSUUID UUID] UUIDString];
    }

    __block BOOL success = NO;
    __block NSString *backend = @"";
    __block NSString *resultMessage = @"desktop_failed:macos_timeout";
    NSMutableDictionary *resultDetails = [NSMutableDictionary dictionary];
    dispatch_semaphore_t completionSemaphore = dispatch_semaphore_create(0);
    if (gNotificationDelegate == nil) {
        gNotificationDelegate = [[NotificationDelegate alloc] init];
    }
    UNUserNotificationCenter *center = [UNUserNotificationCenter currentNotificationCenter];
    [center setDelegate:gNotificationDelegate];

    void (^scheduleRequest)(void) = ^{
        UNMutableNotificationContent *content = [[UNMutableNotificationContent alloc] init];
        [content setTitle:title];
        [content setBody:body];
        [content setSound:[UNNotificationSound defaultSound]];
        UNNotificationRequest *request = [UNNotificationRequest requestWithIdentifier:identifier
                                                                              content:content
                                                                              trigger:nil];
        [center addNotificationRequest:request withCompletionHandler:^(NSError *requestError) {
            if (requestError != nil) {
                resultMessage = @"desktop_failed:macos_request_error";
                [resultDetails addEntriesFromDictionary:NotificationErrorDetails(requestError)];
            } else {
                success = YES;
                backend = backendName ?: @"usernotifications";
                resultMessage = @"desktop_sent";
            }
            dispatch_semaphore_signal(completionSemaphore);
        }];
    };

    __block UNNotificationSettings *notificationSettings = nil;
    dispatch_semaphore_t settingsSemaphore = dispatch_semaphore_create(0);
    [center getNotificationSettingsWithCompletionHandler:^(UNNotificationSettings *settings) {
        notificationSettings = settings;
        dispatch_semaphore_signal(settingsSemaphore);
    }];

    dispatch_time_t settingsDeadline = dispatch_time(DISPATCH_TIME_NOW, (int64_t)(10 * NSEC_PER_SEC));
    if (dispatch_semaphore_wait(settingsSemaphore, settingsDeadline) != 0 || notificationSettings == nil) {
        return NotificationResultPayloadWithDetails(NO, @"desktop_failed:macos_timeout", @"", resultDetails);
    }

    UNAuthorizationStatus authorizationStatus = [notificationSettings authorizationStatus];
    [resultDetails setObject:@((NSInteger)authorizationStatus) forKey:@"authorization_status"];
    [resultDetails setObject:@((NSInteger)[notificationSettings alertSetting]) forKey:@"alert_setting"];
    [resultDetails setObject:@((NSInteger)[notificationSettings notificationCenterSetting])
                      forKey:@"notification_center_setting"];
    [resultDetails setObject:@((NSInteger)[notificationSettings soundSetting]) forKey:@"sound_setting"];

    if (authorizationStatus == UNAuthorizationStatusDenied) {
        return NotificationResultPayloadWithDetails(
            NO,
            @"desktop_failed:macos_permission_denied",
            @"",
            resultDetails
        );
    }

    if (authorizationStatus != UNAuthorizationStatusNotDetermined &&
        ([notificationSettings alertSetting] == UNNotificationSettingDisabled ||
         [notificationSettings notificationCenterSetting] == UNNotificationSettingDisabled)) {
        return NotificationResultPayloadWithDetails(
            NO,
            @"desktop_failed:macos_alert_disabled",
            @"",
            resultDetails
        );
    }

    if (authorizationStatus == UNAuthorizationStatusNotDetermined) {
        UNAuthorizationOptions options = UNAuthorizationOptionAlert | UNAuthorizationOptionSound;
        [center requestAuthorizationWithOptions:options completionHandler:^(BOOL granted, NSError *authorizationError) {
            if (authorizationError != nil) {
                resultMessage = @"desktop_failed:macos_authorization_error";
                [resultDetails addEntriesFromDictionary:NotificationErrorDetails(authorizationError)];
                dispatch_semaphore_signal(completionSemaphore);
                return;
            }
            if (!granted) {
                resultMessage = @"desktop_failed:macos_permission_denied";
                dispatch_semaphore_signal(completionSemaphore);
                return;
            }
            scheduleRequest();
        }];
    } else {
        scheduleRequest();
    }

    dispatch_time_t deadline = dispatch_time(DISPATCH_TIME_NOW, (int64_t)(10 * NSEC_PER_SEC));
    if (dispatch_semaphore_wait(completionSemaphore, deadline) != 0) {
        resultMessage = @"desktop_failed:macos_timeout";
    }
    if (success) {
        if ([NSThread isMainThread]) {
            [[NSRunLoop currentRunLoop] runUntilDate:[NSDate dateWithTimeIntervalSinceNow:1.0]];
        } else {
            dispatch_sync(dispatch_get_main_queue(), ^{
                [[NSRunLoop currentRunLoop] runUntilDate:[NSDate dateWithTimeIntervalSinceNow:1.0]];
            });
        }
    }
    return NotificationResultPayloadWithDetails(success, resultMessage, backend, resultDetails);
}

@interface NotificationSenderAppDelegate : NSObject <NSApplicationDelegate>
@end

@implementation NotificationSenderAppDelegate

- (void)applicationDidFinishLaunching:(NSNotification *)notification {
    (void)notification;
    NSDictionary *result = DeliverNotificationPayload(gNotificationSendPayload, @"launcher_usernotifications");
    WriteNotificationResultPayload(result);
    gNotificationSendExitCode = NotificationResultExitCode(result);
    [NSApp terminate:nil];
}

@end

static int RunNotificationSendMode(void) {
    NSDictionary *payload = ReadNotificationPayload();
    if (payload == nil) {
        WriteNotificationResult(NO, @"desktop_failed:macos_payload_invalid", @"");
        return 2;
    }
    gNotificationSendPayload = payload;
    NSApplication *app = [NSApplication sharedApplication];
    [app setActivationPolicy:NSApplicationActivationPolicyAccessory];
    NotificationSenderAppDelegate *delegate = [[NotificationSenderAppDelegate alloc] init];
    [app setDelegate:delegate];
    [app run];
    (void)delegate;
    return gNotificationSendExitCode;
}

static void DisableSigpipeOnSocket(int fd) {
#ifdef SO_NOSIGPIPE
    int enabled = 1;
    setsockopt(fd, SOL_SOCKET, SO_NOSIGPIPE, &enabled, sizeof(enabled));
#else
    (void)fd;
#endif
}

static void WriteDataToFd(int fd, NSData *data) {
    if (data == nil) {
        return;
    }
    const uint8_t *bytes = (const uint8_t *)[data bytes];
    NSUInteger remaining = [data length];
    while (remaining > 0) {
        ssize_t written = write(fd, bytes, remaining);
        if (written < 0 && errno == EINTR) {
            continue;
        }
        if (written <= 0) {
            break;
        }
        bytes += written;
        remaining -= (NSUInteger)written;
    }
}

static NSData *ReadNotificationClientData(int fd) {
    NSMutableData *data = [NSMutableData data];
    uint8_t buffer[4096];
    while (true) {
        ssize_t bytesRead = read(fd, buffer, sizeof(buffer));
        if (bytesRead > 0) {
            if ([data length] + (NSUInteger)bytesRead > 65536) {
                return nil;
            }
            [data appendBytes:buffer length:(NSUInteger)bytesRead];
            continue;
        }
        if (bytesRead == 0) {
            break;
        }
        if (errno == EINTR) {
            continue;
        }
        return nil;
    }
    return data;
}

static void WriteNotificationResultToFd(int fd, NSDictionary *payload) {
    WriteDataToFd(fd, NotificationResultData(payload));
}

static void HandleNotificationClient(int clientFd) {
    DisableSigpipeOnSocket(clientFd);

    struct timeval timeout;
    timeout.tv_sec = 10;
    timeout.tv_usec = 0;
    setsockopt(clientFd, SOL_SOCKET, SO_RCVTIMEO, &timeout, sizeof(timeout));
    setsockopt(clientFd, SOL_SOCKET, SO_SNDTIMEO, &timeout, sizeof(timeout));

    NSData *data = ReadNotificationClientData(clientFd);
    NSDictionary *payload = NotificationPayloadFromData(data);
    __block NSDictionary *result = nil;
    if (payload == nil) {
        result = NotificationResultPayload(NO, @"desktop_failed:macos_payload_invalid", @"");
    } else {
        result = DeliverNotificationPayload(payload, @"parent_usernotifications");
    }
    WriteNotificationResultToFd(clientFd, result);
    shutdown(clientFd, SHUT_RDWR);
    close(clientFd);
}

static NSString *CreateNotificationSocketDirectory(void) {
    char templatePath[PATH_MAX];
    int written = snprintf(templatePath, sizeof(templatePath), "/tmp/facebook-monitor-notify.XXXXXX");
    if (written <= 0 || written >= (int)sizeof(templatePath)) {
        return nil;
    }
    char *directory = mkdtemp(templatePath);
    if (directory == NULL) {
        return nil;
    }
    chmod(directory, 0700);
    return [NSString stringWithUTF8String:directory];
}

static void RemoveNotificationSocketArtifacts(NSString *socketPath, NSString *directoryPath) {
    if ([socketPath length] > 0) {
        unlink([socketPath fileSystemRepresentation]);
    }
    if ([directoryPath length] > 0) {
        rmdir([directoryPath fileSystemRepresentation]);
    }
}

@interface AppDelegate : NSObject <NSApplicationDelegate>
@property(nonatomic, strong) NSTask *task;
@property(nonatomic, copy) NSString *notificationSocketPath;
@property(nonatomic, copy) NSString *notificationSocketDirectoryPath;
@property(nonatomic, assign) int notificationSocketFd;
@property(nonatomic, strong) dispatch_source_t notificationSocketSource;
@end

@implementation AppDelegate

- (instancetype)init {
    self = [super init];
    if (self) {
        _notificationSocketFd = -1;
    }
    return self;
}

- (BOOL)startNotificationSocket {
    NSString *directoryPath = CreateNotificationSocketDirectory();
    if ([directoryPath length] == 0) {
        return NO;
    }
    NSString *socketPath = [directoryPath stringByAppendingPathComponent:@"notify.sock"];
    int fd = socket(AF_UNIX, SOCK_STREAM, 0);
    if (fd < 0) {
        RemoveNotificationSocketArtifacts(nil, directoryPath);
        return NO;
    }

    int fdFlags = fcntl(fd, F_GETFD, 0);
    if (fdFlags >= 0) {
        fcntl(fd, F_SETFD, fdFlags | FD_CLOEXEC);
    }

    int flags = fcntl(fd, F_GETFL, 0);
    if (flags >= 0) {
        fcntl(fd, F_SETFL, flags | O_NONBLOCK);
    }

    struct sockaddr_un address;
    memset(&address, 0, sizeof(address));
    address.sun_family = AF_UNIX;
    const char *path = [socketPath fileSystemRepresentation];
    if (strlen(path) >= sizeof(address.sun_path)) {
        close(fd);
        RemoveNotificationSocketArtifacts(socketPath, directoryPath);
        return NO;
    }
    strncpy(address.sun_path, path, sizeof(address.sun_path) - 1);
    unlink(path);

    if (bind(fd, (struct sockaddr *)&address, sizeof(address)) != 0 || listen(fd, 8) != 0) {
        close(fd);
        RemoveNotificationSocketArtifacts(socketPath, directoryPath);
        return NO;
    }
    chmod(path, 0600);

    dispatch_source_t source = dispatch_source_create(
        DISPATCH_SOURCE_TYPE_READ,
        (uintptr_t)fd,
        0,
        dispatch_get_global_queue(DISPATCH_QUEUE_PRIORITY_DEFAULT, 0)
    );
    if (source == nil) {
        close(fd);
        RemoveNotificationSocketArtifacts(socketPath, directoryPath);
        return NO;
    }

    self.notificationSocketFd = fd;
    self.notificationSocketPath = socketPath;
    self.notificationSocketDirectoryPath = directoryPath;
    self.notificationSocketSource = source;
    dispatch_source_set_event_handler(source, ^{
        while (true) {
            int clientFd = accept(fd, NULL, NULL);
            if (clientFd < 0) {
                if (errno == EAGAIN || errno == EWOULDBLOCK || errno == EINTR) {
                    break;
                }
                break;
            }
            dispatch_async(dispatch_get_global_queue(DISPATCH_QUEUE_PRIORITY_DEFAULT, 0), ^{
                HandleNotificationClient(clientFd);
            });
        }
    });
    dispatch_resume(source);
    return YES;
}

- (void)stopNotificationSocket {
    if (self.notificationSocketSource != nil) {
        dispatch_source_cancel(self.notificationSocketSource);
        self.notificationSocketSource = nil;
    }
    if (self.notificationSocketFd >= 0) {
        close(self.notificationSocketFd);
        self.notificationSocketFd = -1;
    }
    RemoveNotificationSocketArtifacts(self.notificationSocketPath, self.notificationSocketDirectoryPath);
    self.notificationSocketPath = nil;
    self.notificationSocketDirectoryPath = nil;
}

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
    [environment removeObjectForKey:@"__FACEBOOK_MONITOR_NOTIFICATION_SOCKET_ENV_KEY__"];
    if ([self startNotificationSocket]) {
        [environment setObject:self.notificationSocketPath
                        forKey:@"__FACEBOOK_MONITOR_NOTIFICATION_SOCKET_ENV_KEY__"];
    }

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
        [self stopNotificationSocket];
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
    [self stopNotificationSocket];
    return NSTerminateNow;
}

@end

int main(int argc, const char * argv[]) {
    @autoreleasepool {
        for (int index = 1; index < argc; index++) {
            if (strcmp(argv[index], "__FACEBOOK_MONITOR_NOTIFICATION_SEND_FLAG__") == 0) {
                return RunNotificationSendMode();
            }
        }
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
        .replace(
            "__FACEBOOK_MONITOR_NOTIFICATION_SEND_FLAG__",
            MACOS_APP_BUNDLE_NOTIFICATION_SEND_FLAG,
        )
        .replace(
            "__FACEBOOK_MONITOR_NOTIFICATION_SOCKET_ENV_KEY__",
            MACOS_APP_BUNDLE_NOTIFICATION_SOCKET_ENV,
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
