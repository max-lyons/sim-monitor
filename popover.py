"""
Native macOS popover for the simulation dashboard.
Uses NSPopover + WKWebView to show the Flask dashboard inline
instead of opening a browser window.
"""

import objc
from Foundation import NSObject, NSURL, NSURLRequest, NSMakeRect
from AppKit import (
    NSApplication,
    NSViewController,
    NSPopover,
    NSPopoverBehaviorTransient,
    NSMinYEdge,
)
from WebKit import WKWebView, WKWebViewConfiguration

POPOVER_WIDTH = 520
POPOVER_HEIGHT = 700

# Module-level refs to prevent PyObjC garbage collection
_popover = None
_view_controller = None
_click_handler = None


class WebViewController(NSViewController):
    """NSViewController hosting a WKWebView that loads the dashboard."""

    def initWithURL_(self, url_string):
        self = objc.super(WebViewController, self).init()
        if self is None:
            return None
        self._url_string = url_string
        return self

    def loadView(self):
        config = WKWebViewConfiguration.alloc().init()
        frame = NSMakeRect(0, 0, POPOVER_WIDTH, POPOVER_HEIGHT)
        self._webview = WKWebView.alloc().initWithFrame_configuration_(frame, config)
        self._loadURL()
        self.setView_(self._webview)

    def _loadURL(self):
        url = NSURL.URLWithString_(self._url_string)
        request = NSURLRequest.requestWithURL_(url)
        self._webview.loadRequest_(request)

    def reload(self):
        """Reload the dashboard content."""
        if hasattr(self, '_webview') and self._webview:
            self._webview.reload()


class PopoverClickHandler(NSObject):
    """Handles status bar button clicks to toggle the popover."""

    def initWithPopover_statusItem_(self, popover, status_item):
        self = objc.super(PopoverClickHandler, self).init()
        if self is None:
            return None
        self._popover = popover
        self._status_item = status_item
        return self

    @objc.typedSelector(b'v@:@')
    def togglePopover_(self, sender):
        if self._popover.isShown():
            self._popover.performClose_(sender)
        else:
            # Activate the app so the popover can become key window
            NSApplication.sharedApplication().activateIgnoringOtherApps_(True)

            button = self._status_item.button()
            vc = self._popover.contentViewController()
            if vc:
                vc.reload()
            self._popover.showRelativeToRect_ofView_preferredEdge_(
                button.bounds(), button, NSMinYEdge
            )


def setup_popover(nsstatusitem, port):
    """
    Replace the NSMenu on the status item with a popover.
    Must be called on the main thread after rumps initializes the status bar.
    """
    global _popover, _view_controller, _click_handler

    button = nsstatusitem.button()
    if button is None:
        return

    url = f'http://localhost:{port}?popover=1'
    _view_controller = WebViewController.alloc().initWithURL_(url)

    _popover = NSPopover.alloc().init()
    _popover.setContentSize_((POPOVER_WIDTH, POPOVER_HEIGHT))
    _popover.setBehavior_(NSPopoverBehaviorTransient)
    _popover.setContentViewController_(_view_controller)
    _popover.setAnimates_(False)

    _click_handler = PopoverClickHandler.alloc().initWithPopover_statusItem_(
        _popover, nsstatusitem
    )

    # Remove the menu so clicks send the action instead of showing a menu
    nsstatusitem.setMenu_(None)

    # Wire the button to our click handler
    button.setTarget_(_click_handler)
    button.setAction_(objc.selector(_click_handler.togglePopover_, signature=b'v@:@'))
