# Implement a Non-Disruptive Keep-Awake Feature

## Objective

Implement a small, isolated desktop utility that prevents the display/computer from entering an idle/sleep state while
the feature is enabled.

The implementation must NOT interfere with the user's existing work, cursor position, keyboard input, running
applications, or existing application architecture.

The preferred implementation is to use native OS power-management APIs rather than generating fake mouse or keyboard
input.

If the project already has an established language/framework/architecture, preserve it. Do NOT introduce a new framework
merely to implement this feature.

---

## First: Inspect the Existing Project

Before modifying anything:

1. Inspect the repository structure.
2. Identify:

    * language/runtime
    * application entry point
    * UI framework
    * dependency manager
    * build/package process
    * existing configuration system
    * existing test framework
3. Understand the current architecture.
4. Identify the smallest appropriate integration point.
5. Do not refactor unrelated code.
6. Do not upgrade existing dependencies unless strictly necessary.
7. Do not replace the existing UI framework.

Before writing code, provide a short implementation plan based on what you found.

---

# Functional Requirements

## Core behavior

The application should expose a "Keep Awake" feature with:

* Enabled/disabled state
* Start
* Stop
* Current status
* Optional configurable behavior
* Clean shutdown

When enabled:

* Prevent the display from automatically sleeping.
* Prevent the operating system from treating the application as idle where the native API supports this.
* Do not move the user's mouse.
* Do not generate keyboard input.
* Do not steal focus.
* Do not interfere with other applications.
* Do not continuously consume significant CPU.
* Do not create unnecessary background processes.

When disabled:

* Restore the operating system's previous power-management behavior.

When the application exits:

* Always restore the normal OS power-management state.

---

# Architecture

Create an isolated abstraction similar to:

```text
KeepAwakeService
    start()
    stop()
    is_active()
```

The rest of the application should interact only with this abstraction.

Suggested structure:

```text
src/
  keep_awake/
      __init__
      service
      platform
          windows
          macos
          linux
```

Adapt this structure to the existing repository rather than blindly creating it.

The platform-specific implementation must be isolated.

---

# Windows

Use the native Windows API through the existing language's standard/native FFI mechanism.

For Python, for example, use:

```text
SetThreadExecutionState
```

with the appropriate execution-state flags.

Do not use simulated mouse movement as the primary Windows implementation.

The implementation must:

1. Request that the system/display remain awake when enabled.
2. Release the request when disabled.
3. Release the request on application shutdown.
4. Handle API failure cleanly.
5. Avoid requiring administrator privileges.

Do not modify global Windows registry settings.

Do not change the user's permanent Windows power plan.

Do not change system-wide sleep settings.

The behavior should be temporary and scoped to the running application.

---

# macOS

Use the native macOS mechanism where practical.

A simple implementation may invoke the system's `caffeinate` mechanism if that is the least invasive option.

Requirements:

* Start the keep-awake mechanism when enabled.
* Stop it when disabled.
* Kill/clean up the subprocess on application shutdown.
* Do not alter permanent Energy Saver settings.
* Do not require administrator privileges.

If the existing application is already using native Objective-C/CoreFoundation APIs, prefer integrating with those
rather than spawning a process.

---

# Linux

Use the least invasive mechanism available.

Prefer:

```text
systemd-inhibit
```

when available.

Gracefully handle systems where systemd is unavailable.

Do not assume GNOME, KDE, X11, or Wayland unless the existing application already has such a dependency.

Do not modify permanent desktop power settings.

---

# Optional Mouse-Activity Backend

Only implement this if the product explicitly requires simulated user activity rather than simply keeping the machine
awake.

If implemented, create it as a separate backend:

```text
KeepAwakeBackend
├── NativePowerBackend
└── MouseActivityBackend
```

The mouse backend must:

* Move the cursor by the smallest possible amount.
* Return it to its original position.
* Preserve the user's original cursor position.
* Never click.
* Never type.
* Never steal focus.
* Stop immediately when disabled.

Do not make mouse simulation the default when a native power-management API is available.

---

# UI

Integrate into the existing UI.

Do not redesign the application.

The UI should expose approximately:

```text
Keep Awake

[ OFF / ON ]

Status: Inactive
```

When active:

```text
Keep Awake

[ ON ]

Status: Active
The computer will remain awake while this is enabled.
```

If the application has a system-tray/menu-bar architecture, a tray/menu-bar toggle is preferable to adding a large new
window.

Use the existing application's design system.

---

# Configuration

If the existing application has configuration/preferences, add:

```text
keep_awake:
    enabled: false
```

Optionally support:

```text
mode:
    native
    mouse
```

But do not expose unnecessary complexity in the UI.

The default should be:

```text
enabled = false
mode = native
```

If the application already has a settings/configuration architecture, use it instead of introducing another
configuration mechanism.

---

# Safety / Non-Interference Requirements

This feature must NOT:

* move the mouse by default
* generate keyboard events
* click anything
* change display resolution
* change the system power plan
* modify registry/system configuration
* require administrator privileges
* prevent the user from manually locking the computer
* prevent shutdown/restart
* interfere with fullscreen applications
* continuously poll the system unnecessarily
* modify unrelated application state

Manual user actions such as locking the workstation or shutting down the machine should continue to work normally.

---

# Error Handling

If the native API cannot be initialized:

1. Do not crash the application.
2. Log the underlying error.
3. Show a concise user-facing error.
4. Leave the feature disabled.
5. Restore any partially acquired resources.

Ensure cleanup occurs even when:

* the user closes the application
* an exception occurs
* the UI is destroyed
* the process receives a normal termination signal

---

# Testing

Add automated tests for the platform-independent logic.

At minimum test:

1. Initial state is inactive.
2. `start()` changes state to active.
3. `stop()` changes state to inactive.
4. Calling `start()` twice is safe/idempotent.
5. Calling `stop()` twice is safe/idempotent.
6. Cleanup occurs when the service is destroyed.
7. Backend failures are handled gracefully.
8. The application can continue operating if the backend is unavailable.

For platform-specific code, isolate the native calls behind a mockable interface.

Do NOT require an actual Windows/macOS/Linux desktop environment for unit tests.

If integration tests are practical, clearly separate them from unit tests.

---

# Performance

The feature should have negligible CPU usage.

The native backend should ideally require no periodic polling at all.

If a fallback backend requires a timer:

* use a reasonable interval
* avoid busy loops
* avoid unnecessary wakeups
* clean up the timer when disabled

---

# Dependencies

Do not add a third-party dependency unless necessary.

If the project is Python, prefer:

```text
Python standard library
```

for the native platform integration where possible.

If an additional dependency is genuinely necessary:

1. Explain why.
2. Verify that it is maintained.
3. Keep it narrowly scoped.
4. Add it through the existing dependency-management system.

---

# Logging

Add useful debug logging around:

* service started
* service stopped
* backend selected
* native API failure
* cleanup

Do not log sensitive user information.

Do not log every timer tick if a periodic fallback is used.

---

# Deliverables

Implement the feature completely.

Then provide:

1. Files added.
2. Files modified.
3. Dependencies added, if any.
4. How the feature works on each supported OS.
5. How to run it locally.
6. How to build/package it.
7. Tests added and their results.
8. Any OS-specific limitations.
9. Any assumptions made.

---

# Important Scope Constraint

Do not refactor unrelated parts of the codebase.

Do not change existing behavior outside the keep-awake functionality.

Do not replace existing frameworks.

Do not perform broad dependency upgrades.

Keep the implementation modular enough that it can be removed later without affecting the rest of the application.

Before finishing, inspect the git diff and verify that every changed file is related to this feature.
