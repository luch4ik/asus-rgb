# ASUS HID RGB Control - Fedora RPM Packaging

This document provides instructions on how to build and install the `asus-hidrgb` application on Fedora Linux using the provided RPM spec file.

## 1. Prerequisites

You will need the `rpm-build` and `python3-devel` packages to build the RPM. You can install them using `dnf`:

```bash
sudo dnf install rpm-build python3-devel
```

You will also need to create the standard directory structure that `rpmbuild` uses:

```bash
mkdir -p ~/rpmbuild/{BUILD,BUILDROOT,RPMS,SOURCES,SPECS,SRPMS}
```

## 2. Prepare the Source Code

The RPM build process requires a source tarball (`.tar.gz`).

1.  **Copy the Application:** Place the main application script, `kbdrgb.py`, in the current directory if it's not already here.
2.  **Create the Tarball:** Run the following commands to create the source archive and place it in the correct directory for `rpmbuild`:

    ```bash
    # Create a temporary directory for the source
    mkdir -p asus-hidrgb-1.0.0

    # Copy the main script into the directory
    cp kbdrgb.py asus-hidrgb-1.0.0/

    # Create the compressed tarball
    tar -czvf asus-hidrgb-1.0.0.tar.gz asus-hidrgb-1.0.0/

    # Move the tarball to the SOURCES directory
    mv asus-hidrgb-1.0.0.tar.gz ~/rpmbuild/SOURCES/

    # Clean up the temporary directory
    rm -rf asus-hidrgb-1.0.0
    ```

## 3. Prepare Build Files

Copy the `.spec`, `.desktop`, icon, and `udev` rule files into the `rpmbuild` directory structure:

```bash
# Copy the .spec file
cp asus-hidrgb.spec ~/rpmbuild/SPECS/

# Create a temporary directory in SOURCES for other assets
mkdir -p ~/rpmbuild/SOURCES/assets

# Copy the desktop entry, icon, and udev rule
cp asus-hidrgb.desktop ~/rpmbuild/SOURCES/assets/
cp asus-hidrgb-logo.svg ~/rpmbuild/SOURCES/assets/
cp ../udev/99-kbdrgb.rules ~/rpmbuild/SOURCES/assets/
```
*Note: The `.spec` file is configured to look for these extra files in the `SOURCES` directory during the build process.*

## 4. Build the RPM

With all the files in place, you can now build the RPM package:

```bash
rpmbuild -ba ~/rpmbuild/SPECS/asus-hidrgb.spec
```

If the build is successful, the final `.rpm` file will be located in `~/rpmbuild/RPMS/noarch/`.

## 5. Install and Run

1.  **Install the RPM:** Navigate to the directory containing the RPM and install it using `dnf`. This will also install the necessary dependencies (`python3-qt6`, `hidapi`).

    ```bash
    # The exact path may vary depending on your architecture
    sudo dnf install ~/rpmbuild/RPMS/noarch/asus-hidrgb-1.0.0-1.fc*.noarch.rpm
    ```

2.  **Apply Udev Rule:** For the application to access the keyboard hardware without root privileges, you need to reload the `udev` rules. Unplug and replug your keyboard, or run:

    ```bash
    sudo udevadm control --reload-rules && sudo udevadm trigger
    ```

3.  **Run the Application:** You can now find "ASUS HID RGB Control" in your GNOME applications menu, or you can run it from the terminal:

    ```bash
    asus-hidrgb
    ```
