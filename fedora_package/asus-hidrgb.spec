# Define metadata for the package
Name:           asus-hidrgb
Version:        1.0.0
Release:        1%{?dist}
Summary:        Control RGB lighting on ASUS keyboards
License:        MIT
URL:            https://github.com/your-repo/asus-hidrgb  # Please update this URL

# Define the source code archive
Source0:        .

# Define build and runtime dependencies
BuildRequires:  python3-devel
Requires:       python3-qt6
Requires:       hidapi
Requires:       udev
Requires:       gnome-shell

%description
A graphical utility to control the RGB lighting on ASUS keyboards via the HID interface on Linux. This package also includes a GNOME Shell extension for quick access to lighting controls.

%prep
# No prep needed as we are using the current directory as the source

%build
# This is a Python script, so no build steps are necessary.

%install
# Create the necessary directories in the build root
install -d -m 755 %{buildroot}%{_bindir}
install -d -m 755 %{buildroot}%{_datadir}/applications
install -d -m 755 %{buildroot}%{_datadir}/icons/hicolor/scalable/apps
install -d -m 755 %{buildroot}/usr/lib/udev/rules.d
install -d -m 755 %{buildroot}%{_datadir}/gnome-shell/extensions/asus-keyboard-rgb-control@nvx.com

# Copy the application files to their final destinations
install -D -m 755 ../kbdrgb.py %{buildroot}%{_bindir}/asus-hidrgb
install -D -m 644 asus-hidrgb.desktop %{buildroot}%{_datadir}/applications/asus-hidrgb.desktop
install -D -m 644 asus-hidrgb-logo.svg %{buildroot}%{_datadir}/icons/hicolor/scalable/apps/asus-hidrgb-logo.svg
install -D -m 644 99-kbdrgb.rules %{buildroot}/usr/lib/udev/rules.d/99-asus-hidrgb.rules
install -D -m 644 ../gnome-shell-extension/metadata.json %{buildroot}%{_datadir}/gnome-shell/extensions/asus-keyboard-rgb-control@nvx.com/metadata.json
install -D -m 644 ../gnome-shell-extension/extension.js %{buildroot}%{_datadir}/gnome-shell/extensions/asus-keyboard-rgb-control@nvx.com/extension.js


%files
# List all the files that will be included in the RPM
%{_bindir}/asus-hidrgb
%{_datadir}/applications/asus-hidrgb.desktop
%{_datadir}/icons/hicolor/scalable/apps/asus-hidrgb-logo.svg
/usr/lib/udev/rules.d/99-asus-hidrgb.rules
%{_datadir}/gnome-shell/extensions/asus-keyboard-rgb-control@nvx.com/

%changelog
* Sun Nov 30 2025 Your Name <your.email@example.com> - 1.0.0-1
- Initial RPM release with GNOME Shell extension.
