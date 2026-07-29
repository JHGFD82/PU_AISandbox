# Translation Plugin (base)

The built-in translation plugin, shipped with [PU AI Sandbox](https://github.com/JHGFD82/PU_AISandbox). Nothing to clone or install separately — it's part of the main repository.

It is a **standalone plugin**: it registers the `translate` command, and it serves two roles at once.

1. **Service owner** — it registers the shared translation building blocks (`TranslationService`, `ImageTranslationService`, the prompt-building classes and the translation prompt text) under the `src.*` import paths the rest of the project expects, so any other translation plugin can use the same implementation rather than bundling its own copy.

2. **English handler** — it owns English as a source language, so `translate en-jp` routes here, and it provides English destination-side guidance. It also holds `_execute_translate()`, the shared execution logic every translation plugin delegates to once it has done its own language-specific setup.

---

## How dispatch works

When an **extension plugin** such as `plugins/translation-ea/` is also installed, the plugin loader merges both into a `DispatchPlugin`. Each request is routed by its source language: this plugin drives anything starting from English, and each extension drives the languages it declares in `handles`.

An extension plugin must **not** call `register_subparsers()` — `translate` is already registered here, and registering it twice is a conflict that makes the loader silently skip the extension. Extensions hook in by declaring `handles` and implementing `register_command_flags()` instead.

See [`src/runtime/dispatch_plugin.py`](../../src/runtime/dispatch_plugin.py) for the mechanism and [`docs/plugin-authoring-guide.md`](../../docs/plugin-authoring-guide.md#writing-an-extension-plugin) for the walkthrough.

---

## Writing a language extension

This plugin's `plugin.py` is also the reference template for extension authors — its module docstring is a step-by-step guide. In outline:

1. Copy `plugins/translation/plugin.py` into your own plugin directory.
2. Remove the `_register()` block. This plugin already registers the shared modules, and plugins load alphabetically, so they're in place by the time yours runs.
3. Set `handles` to the source-language codes you own.
4. Implement `register_command_flags()` for your own flags. **Do not implement `register_subparsers()`.**
5. In `run()`, append any language-specific guidance to `sandbox.translation_service.variant_notes` before calling `_execute_translate()`.
6. Optionally implement `get_peer_guidance(token)` to contribute conventions when your language is the *destination* rather than the source.
7. Optionally call `register_extension_ui_hooks()` to add your own fields to the web interface's translate form.

---

## Settings

`plugins/translation/settings.toml` holds this plugin's defaults. `translation-ea` ships an identical file.

```toml
[translation]
temperature = 0.5          # how varied the wording is
top_p = 0.5                # another way of controlling variety
max_tokens = 4000          # longest response per page
context_percentage = 0.65  # how much of the previous page is carried forward

[image_translation]
temperature = 0.3          # slightly varied, to read ambiguous characters from context
max_tokens = 8000          # higher: the output holds a transcript and a translation
```

`src/settings.py` finds this file by walking up from itself to the nearest `settings.toml` containing a `[translation]` or `[image_translation]` section. Its constants are registered as `pu_plugin.translation.settings`, which makes them importable anywhere as `from src.settings import TRANSLATION_TEMPERATURE`.

There is no personal-override file for plugin settings — edit this one directly.

---

## Usage

```bash
python main.py <netid> translate <source>-<target> [options]
```

Language codes are the short forms registered by the installed plugins: `en` here, plus whatever an extension adds (`jp`, `zh`, …). `python main.py --help` lists them all.

```bash
# English to Japanese, PDF in, PDF out:
python main.py jh43 translate en-jp -i article.pdf -o article_ja.pdf

# A Word document, keeping its embedded images:
python main.py jh43 translate en-jp -i paper.docx -o paper_ja.docx --preserve-media

# Only part of a book:
python main.py jh43 translate en-jp -i book.pdf -p 5-10 -o ch5-10_ja.txt

# Paste the text instead of pointing at a file:
python main.py jh43 translate en-jp -c

# Four pages at a time:
python main.py jh43 translate en-jp -i long.pdf -o long_ja.pdf -w 4

# See the prompts without spending anything:
python main.py jh43 translate en-jp -i article.pdf --dry-run
```

Every flag `translate` accepts is documented once, in [`docs/cli-reference.md`](../../docs/cli-reference.md#translate--document-translation) — input and output formats, page ranges, document structure options, fonts, workers, model overrides and prompt notes. It isn't repeated here so the two can't disagree.
