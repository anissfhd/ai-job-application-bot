# extensions/

Put the **NopeCHA** CAPTCHA-solver extension here, in a folder named `nopecha`:

```
extensions/
└── nopecha/
    ├── manifest.json
    └── …
```

It is not included in this repository (third-party code). Download `chromium_automation.zip` from the
official releases page, <https://github.com/NopeCHALLC/nopecha-extension/releases> (release 0.6.1 or
later), and extract its contents here. Full instructions: [INSTALLATION.md, step 4](../INSTALLATION.md#step-4--add-the-nopecha-extension-optional).

The extension is optional. Without it, set `nopecha:` → `actif: false` in `config/config.yaml`.
