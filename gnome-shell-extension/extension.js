
const St = imports.gi.St;
const Main = imports.ui.main;
const PanelMenu = imports.ui.panelMenu;
const PopupMenu = imports.ui.popupMenu;
const GLib = imports.gi.GLib;
const Gio = imports.gi.Gio;

class Extension {
    constructor(uuid) {
        this._uuid = uuid;
    }

    enable() {
        this._indicator = new PanelMenu.Button(0.0, 'ASUS Keyboard RGB Control', false);

        let icon = new St.Icon({
            icon_name: 'input-keyboard-symbolic',
            style_class: 'system-status-icon'
        });

        this._indicator.add_child(icon);
        this._indicator.connect('button-press-event', () => this._showMenu());

        this._menu = new PopupMenu.PopupMenu(this._indicator.actor, 0.0, St.Side.BOTTOM);
        this._menu.actor.add_style_class_name('panel-menu');
        this._menu.connect('open-state-changed', (menu, open) => {
            if (open) {
                // menu is opening
            } else {
                // menu is closing
            }
        });

        this._createMenu();

        Main.panel.addToStatusArea(this._uuid, this._indicator);
    }

    disable() {
        this._indicator.destroy();
        this._indicator = null;
        this._menu.destroy();
        this._menu = null;
    }

    _showMenu() {
        this._menu.open();
    }

    _createMenu() {
        // Static color
        let staticMenuItem = new PopupMenu.PopupMenuItem('Static');
        staticMenuItem.connect('activate', () => {
            this._runCommand('static');
            this._menu.close();
        });
        this._menu.addMenuItem(staticMenuItem);

        // Breathing
        let breathingMenuItem = new PopupMenu.PopupMenuItem('Breathing');
        breathingMenuItem.connect('activate', () => {
            this._runCommand('breathing');
            this._menu.close();
        });
        this._menu.addMenuItem(breathingMenuItem);

        // Rainbow
        let rainbowMenuItem = new PopupMenu.PopupMenuItem('Rainbow');
        rainbowMenuItem.connect('activate', () => {
            this._runCommand('rainbow');
            this._menu.close();
        });
        this._menu.addMenuItem(rainbowMenuItem);
    }

    _runCommand(mode) {
        let command = ['/usr/bin/asus-hidrgb', '--mode', mode];
        try {
            GLib.spawn_async(null, command, null, GLib.SpawnFlags.SEARCH_PATH | GLib.SpawnFlags.DO_NOT_REAP_CHILD, null);
        } catch (e) {
            log(e);
        }
    }
}

function init(meta) {
    return new Extension(meta.uuid);
}
