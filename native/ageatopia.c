#define WIN32_LEAN_AND_MEAN
#define COBJMACROS

#include <windows.h>
#include <commctrl.h>
#include <objbase.h>
#include <shellapi.h>
#include <strsafe.h>
#include <winhttp.h>
#include <WebView2.h>

#include <wchar.h>

#pragma comment(lib, "comctl32.lib")
#pragma comment(lib, "ole32.lib")
#pragma comment(lib, "shell32.lib")
#pragma comment(lib, "user32.lib")
#pragma comment(lib, "winhttp.lib")

#define APP_CLASS_NAME L"Ageatopia.WebView2Host"
#define APP_TITLE L"Ageatopia"
#define APP_URL_CAPACITY 8192
#define APP_HOST_CAPACITY 256
#define APP_DATA_DIR_CAPACITY 32768
#define APP_WEBVIEW_TIMEOUT_MS 30000

#define TIMER_WEBVIEW_STARTUP 1

#define IDM_NAV_BACK 1001
#define IDM_NAV_FORWARD 1002
#define IDM_NAV_RELOAD 1003
#define IDM_FILE_EXIT 2001

typedef struct ParsedUrl {
    INTERNET_SCHEME scheme;
    INTERNET_PORT port;
    WCHAR host[APP_HOST_CAPACITY];
} ParsedUrl;

typedef enum StartupStage {
    STARTUP_STAGE_ENVIRONMENT,
    STARTUP_STAGE_CONTROLLER,
    STARTUP_STAGE_NAVIGATION
} StartupStage;

typedef struct App App;

typedef struct EnvironmentHandler {
    ICoreWebView2CreateCoreWebView2EnvironmentCompletedHandler iface;
    LONG refCount;
    App *app;
} EnvironmentHandler;

typedef struct ControllerHandler {
    ICoreWebView2CreateCoreWebView2ControllerCompletedHandler iface;
    LONG refCount;
    App *app;
} ControllerHandler;

typedef struct NavigationHandler {
    ICoreWebView2NavigationStartingEventHandler iface;
    LONG refCount;
    App *app;
    BOOL isFrame;
} NavigationHandler;

typedef struct NewWindowHandler {
    ICoreWebView2NewWindowRequestedEventHandler iface;
    LONG refCount;
    App *app;
} NewWindowHandler;

typedef struct NavigationCompletedHandler {
    ICoreWebView2NavigationCompletedEventHandler iface;
    LONG refCount;
    App *app;
} NavigationCompletedHandler;

struct App {
    HINSTANCE instance;
    HWND window;
    HWND status;
    ICoreWebView2Environment *environment;
    ICoreWebView2Controller *controller;
    ICoreWebView2 *webview;
    ParsedUrl origin;
    WCHAR initialUrl[APP_URL_CAPACITY];
    WCHAR dataDirectory[APP_DATA_DIR_CAPACITY];
    StartupStage startupStage;
    BOOL closing;
};

typedef enum NavigationTarget {
    NAVIGATION_BLOCKED,
    NAVIGATION_INTERNAL,
    NAVIGATION_EXTERNAL
} NavigationTarget;

static App g_app;

static HRESULT STDMETHODCALLTYPE EnvironmentInvoke(
    ICoreWebView2CreateCoreWebView2EnvironmentCompletedHandler *This,
    HRESULT errorCode,
    ICoreWebView2Environment *result);
static HRESULT STDMETHODCALLTYPE ControllerInvoke(
    ICoreWebView2CreateCoreWebView2ControllerCompletedHandler *This,
    HRESULT errorCode,
    ICoreWebView2Controller *result);
static HRESULT STDMETHODCALLTYPE NavigationInvoke(
    ICoreWebView2NavigationStartingEventHandler *This,
    ICoreWebView2 *sender,
    ICoreWebView2NavigationStartingEventArgs *args);
static HRESULT STDMETHODCALLTYPE NewWindowInvoke(
    ICoreWebView2NewWindowRequestedEventHandler *This,
    ICoreWebView2 *sender,
    ICoreWebView2NewWindowRequestedEventArgs *args);
static HRESULT STDMETHODCALLTYPE NavigationCompletedInvoke(
    ICoreWebView2NavigationCompletedEventHandler *This,
    ICoreWebView2 *sender,
    ICoreWebView2NavigationCompletedEventArgs *args);

#define DEFINE_HANDLER_IDENTITY(prefix, handler_type, interface_type, interface_iid)          \
    static HRESULT STDMETHODCALLTYPE prefix##QueryInterface(                                 \
        interface_type *This, REFIID riid, void **ppvObject)                                   \
    {                                                                                          \
        if (ppvObject == NULL) {                                                               \
            return E_POINTER;                                                                  \
        }                                                                                      \
        *ppvObject = NULL;                                                                     \
        if (!IsEqualIID(riid, &IID_IUnknown) && !IsEqualIID(riid, &interface_iid)) {           \
            return E_NOINTERFACE;                                                              \
        }                                                                                      \
        *ppvObject = This;                                                                     \
        This->lpVtbl->AddRef(This);                                                            \
        return S_OK;                                                                           \
    }                                                                                          \
    static ULONG STDMETHODCALLTYPE prefix##AddRef(interface_type *This)                        \
    {                                                                                          \
        handler_type *handler = (handler_type *)This;                                          \
        return (ULONG)InterlockedIncrement(&handler->refCount);                                \
    }                                                                                          \
    static ULONG STDMETHODCALLTYPE prefix##Release(interface_type *This)                       \
    {                                                                                          \
        handler_type *handler = (handler_type *)This;                                          \
        LONG remaining = InterlockedDecrement(&handler->refCount);                             \
        if (remaining == 0) {                                                                  \
            HeapFree(GetProcessHeap(), 0, handler);                                            \
        }                                                                                      \
        return (ULONG)remaining;                                                               \
    }

DEFINE_HANDLER_IDENTITY(
    EnvironmentHandler_,
    EnvironmentHandler,
    ICoreWebView2CreateCoreWebView2EnvironmentCompletedHandler,
    IID_ICoreWebView2CreateCoreWebView2EnvironmentCompletedHandler)
DEFINE_HANDLER_IDENTITY(
    ControllerHandler_,
    ControllerHandler,
    ICoreWebView2CreateCoreWebView2ControllerCompletedHandler,
    IID_ICoreWebView2CreateCoreWebView2ControllerCompletedHandler)
DEFINE_HANDLER_IDENTITY(
    NavigationHandler_,
    NavigationHandler,
    ICoreWebView2NavigationStartingEventHandler,
    IID_ICoreWebView2NavigationStartingEventHandler)
DEFINE_HANDLER_IDENTITY(
    NewWindowHandler_,
    NewWindowHandler,
    ICoreWebView2NewWindowRequestedEventHandler,
    IID_ICoreWebView2NewWindowRequestedEventHandler)
DEFINE_HANDLER_IDENTITY(
    NavigationCompletedHandler_,
    NavigationCompletedHandler,
    ICoreWebView2NavigationCompletedEventHandler,
    IID_ICoreWebView2NavigationCompletedEventHandler)

static ICoreWebView2CreateCoreWebView2EnvironmentCompletedHandlerVtbl
    ENVIRONMENT_HANDLER_VTBL = {
        EnvironmentHandler_QueryInterface,
        EnvironmentHandler_AddRef,
        EnvironmentHandler_Release,
        EnvironmentInvoke};

static ICoreWebView2CreateCoreWebView2ControllerCompletedHandlerVtbl
    CONTROLLER_HANDLER_VTBL = {
        ControllerHandler_QueryInterface,
        ControllerHandler_AddRef,
        ControllerHandler_Release,
        ControllerInvoke};

static ICoreWebView2NavigationStartingEventHandlerVtbl NAVIGATION_HANDLER_VTBL = {
    NavigationHandler_QueryInterface,
    NavigationHandler_AddRef,
    NavigationHandler_Release,
    NavigationInvoke};

static ICoreWebView2NewWindowRequestedEventHandlerVtbl NEW_WINDOW_HANDLER_VTBL = {
    NewWindowHandler_QueryInterface,
    NewWindowHandler_AddRef,
    NewWindowHandler_Release,
    NewWindowInvoke};

static ICoreWebView2NavigationCompletedEventHandlerVtbl
    NAVIGATION_COMPLETED_HANDLER_VTBL = {
        NavigationCompletedHandler_QueryInterface,
        NavigationCompletedHandler_AddRef,
        NavigationCompletedHandler_Release,
        NavigationCompletedInvoke};

static void SetStatus(App *app, LPCWSTR message)
{
    if (app->status != NULL) {
        SendMessageW(app->status, SB_SETTEXTW, 0, (LPARAM)message);
    }
}

static void ReportFailure(App *app, LPCWSTR message, HRESULT errorCode)
{
    WCHAR details[512];

    SetStatus(app, message);
    if (SUCCEEDED(StringCchPrintfW(
            details,
            ARRAYSIZE(details),
            L"%s\n\nError code: 0x%08lX",
            message,
            (unsigned long)errorCode))) {
        MessageBoxW(
            app->window,
            details,
            APP_TITLE,
            MB_OK | MB_ICONERROR | (app->window == NULL ? MB_TASKMODAL : MB_APPLMODAL));
    }
}

static void FailApp(App *app, LPCWSTR message, HRESULT errorCode)
{
    if (app->window != NULL) {
        KillTimer(app->window, TIMER_WEBVIEW_STARTUP);
    }
    ReportFailure(app, message, errorCode);
    if (app->window != NULL && IsWindow(app->window)) {
        PostMessageW(app->window, WM_CLOSE, 0, 0);
    }
}

static BOOL StartWebViewTimeout(App *app, StartupStage stage, LPCWSTR status)
{
    if (app->window == NULL ||
        SetTimer(app->window, TIMER_WEBVIEW_STARTUP, APP_WEBVIEW_TIMEOUT_MS, NULL) == 0) {
        return FALSE;
    }
    app->startupStage = stage;
    SetStatus(app, status);
    return TRUE;
}

static BOOL ParseHttpUrl(LPCWSTR value, ParsedUrl *parsed)
{
    URL_COMPONENTS components;
    WCHAR scheme[16];
    WCHAR host[APP_HOST_CAPACITY];
    WCHAR userName[256];
    WCHAR password[256];
    size_t valueLength;

    if (value == NULL || parsed == NULL ||
        FAILED(StringCchLengthW(value, APP_URL_CAPACITY, &valueLength)) || valueLength == 0) {
        return FALSE;
    }

    ZeroMemory(&components, sizeof(components));
    ZeroMemory(scheme, sizeof(scheme));
    ZeroMemory(host, sizeof(host));
    ZeroMemory(userName, sizeof(userName));
    ZeroMemory(password, sizeof(password));
    components.dwStructSize = sizeof(components);
    components.lpszScheme = scheme;
    components.dwSchemeLength = ARRAYSIZE(scheme) - 1;
    components.lpszHostName = host;
    components.dwHostNameLength = ARRAYSIZE(host) - 1;
    components.lpszUserName = userName;
    components.dwUserNameLength = ARRAYSIZE(userName) - 1;
    components.lpszPassword = password;
    components.dwPasswordLength = ARRAYSIZE(password) - 1;

    if (!WinHttpCrackUrl(value, 0, 0, &components) ||
        components.dwSchemeLength >= ARRAYSIZE(scheme) ||
        components.dwHostNameLength >= ARRAYSIZE(host) ||
        components.dwUserNameLength != 0 || components.dwPasswordLength != 0 ||
        (components.nScheme != INTERNET_SCHEME_HTTP &&
         components.nScheme != INTERNET_SCHEME_HTTPS)) {
        return FALSE;
    }

    scheme[components.dwSchemeLength] = L'\0';
    host[components.dwHostNameLength] = L'\0';
    if (host[0] == L'\0' || FAILED(StringCchCopyW(parsed->host, ARRAYSIZE(parsed->host), host))) {
        return FALSE;
    }

    parsed->scheme = components.nScheme;
    parsed->port = components.nPort;
    if (parsed->port == 0) {
        parsed->port = parsed->scheme == INTERNET_SCHEME_HTTPS
            ? INTERNET_DEFAULT_HTTPS_PORT
            : INTERNET_DEFAULT_HTTP_PORT;
    }
    return TRUE;
}

static BOOL IsLoopbackHost(LPCWSTR host)
{
    WCHAR normalized[APP_HOST_CAPACITY];
    size_t length;

    if (host == NULL || FAILED(StringCchLengthW(host, ARRAYSIZE(normalized), &length))) {
        return FALSE;
    }

    if (length >= 2 && host[0] == L'[' && host[length - 1] == L']') {
        ++host;
        length -= 2;
    }
    if (length == 0 || length >= ARRAYSIZE(normalized)) {
        return FALSE;
    }
    if (FAILED(StringCchCopyNW(normalized, ARRAYSIZE(normalized), host, length))) {
        return FALSE;
    }

    return CompareStringOrdinal(normalized, -1, L"localhost", -1, TRUE) == CSTR_EQUAL ||
        CompareStringOrdinal(normalized, -1, L"127.0.0.1", -1, TRUE) == CSTR_EQUAL ||
        CompareStringOrdinal(normalized, -1, L"::1", -1, TRUE) == CSTR_EQUAL;
}

static NavigationTarget ClassifyNavigation(App *app, LPCWSTR value)
{
    ParsedUrl candidate;

    if (!ParseHttpUrl(value, &candidate)) {
        return NAVIGATION_BLOCKED;
    }
    if (candidate.scheme == app->origin.scheme && candidate.port == app->origin.port &&
        CompareStringOrdinal(candidate.host, -1, app->origin.host, -1, TRUE) == CSTR_EQUAL) {
        return NAVIGATION_INTERNAL;
    }
    return NAVIGATION_EXTERNAL;
}

static BOOL LaunchExternalUrl(App *app, LPCWSTR value)
{
    HINSTANCE result = ShellExecuteW(app->window, L"open", value, NULL, NULL, SW_SHOWNORMAL);
    return (INT_PTR)result > 32;
}

static void LayoutStatus(App *app)
{
    RECT client;
    int parts[2];

    if (app->status == NULL || app->window == NULL) {
        return;
    }
    SendMessageW(app->status, WM_SIZE, 0, 0);
    if (!GetClientRect(app->window, &client)) {
        return;
    }
    parts[0] = client.right > 150 ? client.right - 150 : 0;
    parts[1] = -1;
    SendMessageW(app->status, SB_SETPARTS, ARRAYSIZE(parts), (LPARAM)parts);
    SendMessageW(app->status, SB_SETTEXTW, 1, (LPARAM)APP_TITLE);
}

static void ResizeWebView(App *app)
{
    RECT bounds;
    RECT statusBounds;

    if (app->controller == NULL || app->window == NULL ||
        !GetClientRect(app->window, &bounds)) {
        return;
    }

    if (app->status != NULL && GetWindowRect(app->status, &statusBounds)) {
        MapWindowPoints(NULL, app->window, (POINT *)&statusBounds, 2);
        if (statusBounds.top < bounds.bottom) {
            bounds.bottom = statusBounds.top;
        }
    }
    if (bounds.right < bounds.left) {
        bounds.right = bounds.left;
    }
    if (bounds.bottom < bounds.top) {
        bounds.bottom = bounds.top;
    }
    ICoreWebView2Controller_put_Bounds(app->controller, bounds);
}

static void ReleaseAppObjects(App *app)
{
    if (app->controller != NULL) {
        ICoreWebView2Controller_Close(app->controller);
    }
    if (app->webview != NULL) {
        ICoreWebView2_Release(app->webview);
        app->webview = NULL;
    }
    if (app->controller != NULL) {
        ICoreWebView2Controller_Release(app->controller);
        app->controller = NULL;
    }
    if (app->environment != NULL) {
        ICoreWebView2Environment_Release(app->environment);
        app->environment = NULL;
    }
}

static EnvironmentHandler *CreateEnvironmentHandler(App *app)
{
    EnvironmentHandler *handler = (EnvironmentHandler *)HeapAlloc(
        GetProcessHeap(), HEAP_ZERO_MEMORY, sizeof(*handler));
    if (handler != NULL) {
        handler->iface.lpVtbl = &ENVIRONMENT_HANDLER_VTBL;
        handler->refCount = 1;
        handler->app = app;
    }
    return handler;
}

static ControllerHandler *CreateControllerHandler(App *app)
{
    ControllerHandler *handler = (ControllerHandler *)HeapAlloc(
        GetProcessHeap(), HEAP_ZERO_MEMORY, sizeof(*handler));
    if (handler != NULL) {
        handler->iface.lpVtbl = &CONTROLLER_HANDLER_VTBL;
        handler->refCount = 1;
        handler->app = app;
    }
    return handler;
}

static NavigationHandler *CreateNavigationHandler(App *app, BOOL isFrame)
{
    NavigationHandler *handler = (NavigationHandler *)HeapAlloc(
        GetProcessHeap(), HEAP_ZERO_MEMORY, sizeof(*handler));
    if (handler != NULL) {
        handler->iface.lpVtbl = &NAVIGATION_HANDLER_VTBL;
        handler->refCount = 1;
        handler->app = app;
        handler->isFrame = isFrame;
    }
    return handler;
}

static NewWindowHandler *CreateNewWindowHandler(App *app)
{
    NewWindowHandler *handler = (NewWindowHandler *)HeapAlloc(
        GetProcessHeap(), HEAP_ZERO_MEMORY, sizeof(*handler));
    if (handler != NULL) {
        handler->iface.lpVtbl = &NEW_WINDOW_HANDLER_VTBL;
        handler->refCount = 1;
        handler->app = app;
    }
    return handler;
}

static NavigationCompletedHandler *CreateNavigationCompletedHandler(App *app)
{
    NavigationCompletedHandler *handler = (NavigationCompletedHandler *)HeapAlloc(
        GetProcessHeap(), HEAP_ZERO_MEMORY, sizeof(*handler));
    if (handler != NULL) {
        handler->iface.lpVtbl = &NAVIGATION_COMPLETED_HANDLER_VTBL;
        handler->refCount = 1;
        handler->app = app;
    }
    return handler;
}

static HRESULT RegisterNavigationHandlers(App *app)
{
    HRESULT hr;
    EventRegistrationToken token;
    NavigationHandler *navigation = CreateNavigationHandler(app, FALSE);
    if (navigation == NULL) {
        return E_OUTOFMEMORY;
    }
    hr = ICoreWebView2_add_NavigationStarting(
        app->webview, &navigation->iface, &token);
    ICoreWebView2NavigationStartingEventHandler_Release(&navigation->iface);
    if (FAILED(hr)) {
        return hr;
    }

    NavigationHandler *frameNavigation = CreateNavigationHandler(app, TRUE);
    if (frameNavigation == NULL) {
        return E_OUTOFMEMORY;
    }
    hr = ICoreWebView2_add_FrameNavigationStarting(
        app->webview, &frameNavigation->iface, &token);
    ICoreWebView2NavigationStartingEventHandler_Release(&frameNavigation->iface);
    if (FAILED(hr)) {
        return hr;
    }

    NewWindowHandler *newWindow = CreateNewWindowHandler(app);
    if (newWindow == NULL) {
        return E_OUTOFMEMORY;
    }
    hr = ICoreWebView2_add_NewWindowRequested(app->webview, &newWindow->iface, &token);
    ICoreWebView2NewWindowRequestedEventHandler_Release(&newWindow->iface);
    if (FAILED(hr)) {
        return hr;
    }

    NavigationCompletedHandler *completed = CreateNavigationCompletedHandler(app);
    if (completed == NULL) {
        return E_OUTOFMEMORY;
    }
    hr = ICoreWebView2_add_NavigationCompleted(
        app->webview, &completed->iface, &token);
    ICoreWebView2NavigationCompletedEventHandler_Release(&completed->iface);
    return hr;
}

static HRESULT STDMETHODCALLTYPE EnvironmentInvoke(
    ICoreWebView2CreateCoreWebView2EnvironmentCompletedHandler *This,
    HRESULT errorCode,
    ICoreWebView2Environment *result)
{
    EnvironmentHandler *handler = (EnvironmentHandler *)This;
    App *app = handler->app;
    ControllerHandler *controllerHandler;
    HRESULT hr;

    if (app->closing || app->window == NULL || !IsWindow(app->window)) {
        return S_OK;
    }
    if (FAILED(errorCode) || result == NULL) {
        FailApp(app, L"The WebView2 runtime could not be started.", FAILED(errorCode) ? errorCode : E_FAIL);
        return S_OK;
    }

    ICoreWebView2Environment_AddRef(result);
    app->environment = result;
    if (!StartWebViewTimeout(
            app,
            STARTUP_STAGE_CONTROLLER,
            L"Creating the WebView2 browser...")) {
        FailApp(app, L"WebView2 startup could not be monitored.", HRESULT_FROM_WIN32(GetLastError()));
        return S_OK;
    }
    controllerHandler = CreateControllerHandler(app);
    if (controllerHandler == NULL) {
        FailApp(app, L"The WebView2 controller could not be created.", E_OUTOFMEMORY);
        return S_OK;
    }
    hr = ICoreWebView2Environment_CreateCoreWebView2Controller(
        app->environment, app->window, &controllerHandler->iface);
    ICoreWebView2CreateCoreWebView2ControllerCompletedHandler_Release(
        &controllerHandler->iface);
    if (FAILED(hr)) {
        FailApp(app, L"The WebView2 controller could not be created.", hr);
    }
    return S_OK;
}

static HRESULT STDMETHODCALLTYPE ControllerInvoke(
    ICoreWebView2CreateCoreWebView2ControllerCompletedHandler *This,
    HRESULT errorCode,
    ICoreWebView2Controller *result)
{
    ControllerHandler *handler = (ControllerHandler *)This;
    App *app = handler->app;
    HRESULT hr;

    if (app->closing || app->window == NULL || !IsWindow(app->window)) {
        return S_OK;
    }
    if (FAILED(errorCode) || result == NULL) {
        FailApp(app, L"The WebView2 controller could not be created.", FAILED(errorCode) ? errorCode : E_FAIL);
        return S_OK;
    }

    ICoreWebView2Controller_AddRef(result);
    app->controller = result;
    hr = ICoreWebView2Controller_get_CoreWebView2(app->controller, &app->webview);
    if (FAILED(hr) || app->webview == NULL) {
        FailApp(app, L"The WebView2 browser could not be initialized.", FAILED(hr) ? hr : E_FAIL);
        return S_OK;
    }

    hr = RegisterNavigationHandlers(app);
    if (FAILED(hr)) {
        FailApp(app, L"The WebView2 navigation controls could not be initialized.", hr);
        return S_OK;
    }
    ResizeWebView(app);
    hr = ICoreWebView2Controller_put_IsVisible(app->controller, TRUE);
    if (FAILED(hr)) {
        FailApp(app, L"The WebView2 browser could not be displayed.", hr);
        return S_OK;
    }
    if (!StartWebViewTimeout(
            app,
            STARTUP_STAGE_NAVIGATION,
            L"Opening the local dashboard...")) {
        FailApp(app, L"WebView2 startup could not be monitored.", HRESULT_FROM_WIN32(GetLastError()));
        return S_OK;
    }
    hr = ICoreWebView2_Navigate(app->webview, app->initialUrl);
    if (FAILED(hr)) {
        FailApp(app, L"The local Ageatopia page could not be opened.", hr);
    }
    return S_OK;
}

static HRESULT STDMETHODCALLTYPE NavigationInvoke(
    ICoreWebView2NavigationStartingEventHandler *This,
    ICoreWebView2 *sender,
    ICoreWebView2NavigationStartingEventArgs *args)
{
    NavigationHandler *handler = (NavigationHandler *)This;
    App *app = handler->app;
    LPWSTR uri = NULL;
    HRESULT hr;
    NavigationTarget target;

    UNREFERENCED_PARAMETER(sender);
    if (args == NULL) {
        return E_POINTER;
    }
    hr = ICoreWebView2NavigationStartingEventArgs_get_Uri(args, &uri);
    if (FAILED(hr) || uri == NULL) {
        ICoreWebView2NavigationStartingEventArgs_put_Cancel(args, TRUE);
        SetStatus(app, L"Blocked an unsupported navigation target.");
        CoTaskMemFree(uri);
        return S_OK;
    }

    target = ClassifyNavigation(app, uri);
    if (target == NAVIGATION_INTERNAL) {
        if (!StartWebViewTimeout(
                app,
                STARTUP_STAGE_NAVIGATION,
                L"Loading Ageatopia...")) {
            ICoreWebView2NavigationStartingEventArgs_put_Cancel(args, TRUE);
            FailApp(app, L"WebView2 startup could not be monitored.", HRESULT_FROM_WIN32(GetLastError()));
        }
    } else {
        ICoreWebView2NavigationStartingEventArgs_put_Cancel(args, TRUE);
        if (handler->isFrame) {
            SetStatus(app, L"Blocked content from another site.");
        } else if (target == NAVIGATION_EXTERNAL) {
            SetStatus(app, LaunchExternalUrl(app, uri)
                ? L"Opened the link in your browser."
                : L"Could not open the link in your browser.");
        } else {
            SetStatus(app, L"Blocked an unsupported navigation target.");
        }
    }
    CoTaskMemFree(uri);
    return S_OK;
}

static HRESULT STDMETHODCALLTYPE NewWindowInvoke(
    ICoreWebView2NewWindowRequestedEventHandler *This,
    ICoreWebView2 *sender,
    ICoreWebView2NewWindowRequestedEventArgs *args)
{
    NewWindowHandler *handler = (NewWindowHandler *)This;
    App *app = handler->app;
    LPWSTR uri = NULL;
    HRESULT hr;

    UNREFERENCED_PARAMETER(sender);
    if (args == NULL) {
        return E_POINTER;
    }
    hr = ICoreWebView2NewWindowRequestedEventArgs_put_Handled(args, TRUE);
    if (FAILED(hr)) {
        return hr;
    }

    hr = ICoreWebView2NewWindowRequestedEventArgs_get_Uri(args, &uri);
    if (FAILED(hr) || uri == NULL) {
        SetStatus(app, L"Blocked an unsupported new window.");
        CoTaskMemFree(uri);
        return S_OK;
    }

    NavigationTarget target = ClassifyNavigation(app, uri);
    if (target == NAVIGATION_INTERNAL) {
        hr = ICoreWebView2_Navigate(app->webview, uri);
        if (FAILED(hr)) {
            SetStatus(app, L"The page could not be opened.");
        }
    } else if (target == NAVIGATION_EXTERNAL) {
        SetStatus(app, LaunchExternalUrl(app, uri)
            ? L"Opened the link in your browser."
            : L"Could not open the link in your browser.");
    } else {
        SetStatus(app, L"Blocked an unsupported new window.");
    }
    CoTaskMemFree(uri);
    return S_OK;
}

static HRESULT STDMETHODCALLTYPE NavigationCompletedInvoke(
    ICoreWebView2NavigationCompletedEventHandler *This,
    ICoreWebView2 *sender,
    ICoreWebView2NavigationCompletedEventArgs *args)
{
    BOOL succeeded = FALSE;

    UNREFERENCED_PARAMETER(sender);
    if (args != NULL &&
        SUCCEEDED(ICoreWebView2NavigationCompletedEventArgs_get_IsSuccess(args, &succeeded))) {
        App *app = ((NavigationCompletedHandler *)This)->app;
        if (app->window != NULL) {
            KillTimer(app->window, TIMER_WEBVIEW_STARTUP);
        }
        SetStatus(app, succeeded ? L"Ready" : L"The local page could not be loaded.");
    }
    return S_OK;
}

static HMENU CreateAppMenu(void)
{
    HMENU menu = CreateMenu();
    HMENU navigate = CreatePopupMenu();
    HMENU file = CreatePopupMenu();

    if (menu == NULL || navigate == NULL || file == NULL) {
        if (menu != NULL) {
            DestroyMenu(menu);
        }
        if (navigate != NULL) {
            DestroyMenu(navigate);
        }
        if (file != NULL) {
            DestroyMenu(file);
        }
        return NULL;
    }

    if (!AppendMenuW(navigate, MF_STRING, IDM_NAV_BACK, L"&Back") ||
        !AppendMenuW(navigate, MF_STRING, IDM_NAV_FORWARD, L"&Forward") ||
        !AppendMenuW(navigate, MF_SEPARATOR, 0, NULL) ||
        !AppendMenuW(navigate, MF_STRING, IDM_NAV_RELOAD, L"&Reload") ||
        !AppendMenuW(file, MF_STRING, IDM_FILE_EXIT, L"E&xit")) {
        DestroyMenu(menu);
        DestroyMenu(navigate);
        DestroyMenu(file);
        return NULL;
    }

    if (!AppendMenuW(menu, MF_POPUP, (UINT_PTR)navigate, L"&Navigate")) {
        DestroyMenu(menu);
        DestroyMenu(navigate);
        DestroyMenu(file);
        return NULL;
    }
    if (!AppendMenuW(menu, MF_POPUP, (UINT_PTR)file, L"&File")) {
        DestroyMenu(menu);
        DestroyMenu(file);
        return NULL;
    }
    return menu;
}

static void UpdateNavigationMenu(HWND window)
{
    HMENU menu = GetMenu(window);
    BOOL canGoBack = FALSE;
    BOOL canGoForward = FALSE;

    if (menu == NULL) {
        return;
    }
    if (g_app.webview != NULL) {
        ICoreWebView2_get_CanGoBack(g_app.webview, &canGoBack);
        ICoreWebView2_get_CanGoForward(g_app.webview, &canGoForward);
    }
    EnableMenuItem(menu, IDM_NAV_BACK, MF_BYCOMMAND | (canGoBack ? MF_ENABLED : MF_GRAYED));
    EnableMenuItem(
        menu,
        IDM_NAV_FORWARD,
        MF_BYCOMMAND | (canGoForward ? MF_ENABLED : MF_GRAYED));
}

static LRESULT CALLBACK WindowProc(HWND window, UINT message, WPARAM wParam, LPARAM lParam)
{
    switch (message) {
    case WM_CREATE:
        g_app.window = window;
        g_app.status = CreateWindowExW(
            0,
            STATUSCLASSNAMEW,
            NULL,
            WS_CHILD | WS_VISIBLE | SBARS_SIZEGRIP,
            0,
            0,
            0,
            0,
            window,
            (HMENU)1,
            g_app.instance,
            NULL);
        if (g_app.status == NULL) {
            return -1;
        }
        SetStatus(&g_app, L"Starting WebView2...");
        LayoutStatus(&g_app);
        return 0;

    case WM_SIZE:
        LayoutStatus(&g_app);
        ResizeWebView(&g_app);
        return 0;

    case WM_DPICHANGED: {
        const RECT *suggested = (const RECT *)lParam;
        if (suggested != NULL) {
            SetWindowPos(
                window,
                NULL,
                suggested->left,
                suggested->top,
                suggested->right - suggested->left,
                suggested->bottom - suggested->top,
                SWP_NOZORDER | SWP_NOACTIVATE);
        }
        LayoutStatus(&g_app);
        ResizeWebView(&g_app);
        return 0;
    }

    case WM_INITMENUPOPUP:
        UpdateNavigationMenu(window);
        return 0;

    case WM_TIMER:
        if (wParam == TIMER_WEBVIEW_STARTUP) {
            if (g_app.startupStage == STARTUP_STAGE_ENVIRONMENT) {
                FailApp(&g_app, L"The WebView2 runtime did not start within 30 seconds.", HRESULT_FROM_WIN32(ERROR_TIMEOUT));
            } else if (g_app.startupStage == STARTUP_STAGE_CONTROLLER) {
                FailApp(&g_app, L"The WebView2 browser did not initialize within 30 seconds.", HRESULT_FROM_WIN32(ERROR_TIMEOUT));
            } else {
                FailApp(&g_app, L"The local dashboard did not finish loading within 30 seconds.", HRESULT_FROM_WIN32(ERROR_TIMEOUT));
            }
            return 0;
        }
        break;

    case WM_COMMAND:
        switch (LOWORD(wParam)) {
        case IDM_NAV_BACK:
            if (g_app.webview != NULL) {
                ICoreWebView2_GoBack(g_app.webview);
            }
            return 0;
        case IDM_NAV_FORWARD:
            if (g_app.webview != NULL) {
                ICoreWebView2_GoForward(g_app.webview);
            }
            return 0;
        case IDM_NAV_RELOAD:
            if (g_app.webview != NULL) {
                ICoreWebView2_Reload(g_app.webview);
            }
            return 0;
        case IDM_FILE_EXIT:
            PostMessageW(window, WM_CLOSE, 0, 0);
            return 0;
        default:
            break;
        }
        break;

    case WM_DESTROY:
        g_app.closing = TRUE;
        KillTimer(window, TIMER_WEBVIEW_STARTUP);
        ReleaseAppObjects(&g_app);
        PostQuitMessage(0);
        return 0;

    case WM_NCDESTROY:
        g_app.window = NULL;
        g_app.status = NULL;
        break;

    default:
        break;
    }
    return DefWindowProcW(window, message, wParam, lParam);
}

static BOOL ParseCommandLine(int argc, LPWSTR *argv)
{
    BOOL haveUrl = FALSE;
    BOOL haveDataDirectory = FALSE;
    int index;
    DWORD pathLength;

    for (index = 1; index < argc; ++index) {
        if (wcscmp(argv[index], L"--url") == 0 && index + 1 < argc && !haveUrl) {
            if (FAILED(StringCchCopyW(
                    g_app.initialUrl, ARRAYSIZE(g_app.initialUrl), argv[index + 1]))) {
                return FALSE;
            }
            haveUrl = TRUE;
            ++index;
        } else if (wcscmp(argv[index], L"--data-dir") == 0 &&
            index + 1 < argc && !haveDataDirectory) {
            pathLength = GetFullPathNameW(
                argv[index + 1],
                ARRAYSIZE(g_app.dataDirectory),
                g_app.dataDirectory,
                NULL);
            if (pathLength == 0 || pathLength >= ARRAYSIZE(g_app.dataDirectory)) {
                return FALSE;
            }
            haveDataDirectory = TRUE;
            ++index;
        } else {
            return FALSE;
        }
    }

    return haveUrl && haveDataDirectory && ParseHttpUrl(g_app.initialUrl, &g_app.origin) &&
        IsLoopbackHost(g_app.origin.host);
}

static HMENU CreateWindowMenuOrReport(void)
{
    HMENU menu = CreateAppMenu();
    if (menu == NULL) {
        MessageBoxW(NULL, L"The native menus could not be created.", APP_TITLE, MB_OK | MB_ICONERROR);
    }
    return menu;
}

int WINAPI wWinMain(HINSTANCE instance, HINSTANCE previousInstance, PWSTR commandLine, int showCommand)
{
    INITCOMMONCONTROLSEX controls;
    WNDCLASSEXW windowClass;
    LPWSTR *arguments;
    int argumentCount = 0;
    HMENU menu;
    HWND window;
    HRESULT hr;
    MSG message;
    int messageResult;
    EnvironmentHandler *environmentHandler;

    UNREFERENCED_PARAMETER(previousInstance);
    UNREFERENCED_PARAMETER(commandLine);

    SetProcessDpiAwarenessContext(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2);
    arguments = CommandLineToArgvW(GetCommandLineW(), &argumentCount);
    if (arguments == NULL || !ParseCommandLine(argumentCount, arguments)) {
        if (arguments != NULL) {
            LocalFree(arguments);
        }
        MessageBoxW(
            NULL,
            L"Usage: ageatopia.exe --url <loopback-url> --data-dir <profile-directory>",
            APP_TITLE,
            MB_OK | MB_ICONERROR | MB_TASKMODAL);
        return 2;
    }
    LocalFree(arguments);

    g_app.instance = instance;
    hr = CoInitializeEx(NULL, COINIT_APARTMENTTHREADED);
    if (FAILED(hr)) {
        ReportFailure(&g_app, L"The WebView2 host could not initialize COM.", hr);
        return 1;
    }

    ZeroMemory(&controls, sizeof(controls));
    controls.dwSize = sizeof(controls);
    controls.dwICC = ICC_BAR_CLASSES;
    if (!InitCommonControlsEx(&controls)) {
        ReportFailure(&g_app, L"The native status bar could not be initialized.", HRESULT_FROM_WIN32(GetLastError()));
        CoUninitialize();
        return 1;
    }

    ZeroMemory(&windowClass, sizeof(windowClass));
    windowClass.cbSize = sizeof(windowClass);
    windowClass.hInstance = instance;
    windowClass.lpfnWndProc = WindowProc;
    windowClass.hCursor = LoadCursorW(NULL, IDC_ARROW);
    windowClass.hbrBackground = (HBRUSH)(COLOR_WINDOW + 1);
    windowClass.lpszClassName = APP_CLASS_NAME;
    if (!RegisterClassExW(&windowClass)) {
        ReportFailure(&g_app, L"The Ageatopia window could not be registered.", HRESULT_FROM_WIN32(GetLastError()));
        CoUninitialize();
        return 1;
    }

    menu = CreateWindowMenuOrReport();
    if (menu == NULL) {
        CoUninitialize();
        return 1;
    }
    window = CreateWindowExW(
        WS_EX_APPWINDOW,
        APP_CLASS_NAME,
        APP_TITLE,
        WS_OVERLAPPEDWINDOW | WS_CLIPCHILDREN,
        CW_USEDEFAULT,
        CW_USEDEFAULT,
        1280,
        820,
        NULL,
        menu,
        instance,
        NULL);
    if (window == NULL) {
        DestroyMenu(menu);
        ReportFailure(&g_app, L"The Ageatopia window could not be created.", HRESULT_FROM_WIN32(GetLastError()));
        CoUninitialize();
        return 1;
    }

    ShowWindow(window, showCommand);
    ShowWindow(window, SW_MAXIMIZE);
    UpdateWindow(window);

    environmentHandler = CreateEnvironmentHandler(&g_app);
    if (environmentHandler == NULL) {
        ReportFailure(&g_app, L"The WebView2 runtime could not be started.", E_OUTOFMEMORY);
        DestroyWindow(window);
        CoUninitialize();
        return 1;
    }
    if (!StartWebViewTimeout(
            &g_app,
            STARTUP_STAGE_ENVIRONMENT,
            L"Starting the WebView2 runtime...")) {
        ICoreWebView2CreateCoreWebView2EnvironmentCompletedHandler_Release(
            &environmentHandler->iface);
        ReportFailure(&g_app, L"WebView2 startup could not be monitored.", HRESULT_FROM_WIN32(GetLastError()));
        DestroyWindow(window);
        CoUninitialize();
        return 1;
    }
    hr = CreateCoreWebView2EnvironmentWithOptions(
        NULL,
        g_app.dataDirectory,
        NULL,
        &environmentHandler->iface);
    ICoreWebView2CreateCoreWebView2EnvironmentCompletedHandler_Release(
        &environmentHandler->iface);
    if (FAILED(hr)) {
        ReportFailure(&g_app, L"The WebView2 runtime could not be started.", hr);
        DestroyWindow(window);
        CoUninitialize();
        return 1;
    }

    while ((messageResult = GetMessageW(&message, NULL, 0, 0)) > 0) {
        TranslateMessage(&message);
        DispatchMessageW(&message);
    }
    if (messageResult == -1) {
        ReportFailure(&g_app, L"The Ageatopia message loop stopped unexpectedly.", HRESULT_FROM_WIN32(GetLastError()));
    }
    CoUninitialize();
    return messageResult == -1 ? 1 : (int)message.wParam;
}
