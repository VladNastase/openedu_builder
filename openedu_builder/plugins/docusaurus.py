import itertools
import logging
import os
import re
import shutil
import subprocess
import tempfile
from typing import Any, Mapping
from urllib.parse import unquote

from jinja2 import Environment, PackageLoader
from openedu_builder import path_utils
from openedu_builder.plugins.plugin import Plugin, PluginRunError

log = logging.getLogger(__name__)


AUTO_SIDEBAR = """const sidebars = {{
  {sidebar}: [{{type: 'autogenerated', dirName: '.'}}],
}};

module.exports = sidebars;
"""
DOCS_ONLY_FRONTMATTER = "---\nslug: /\n---\n"
DUMMY_INTRO = """# Introduction
This is a dummy introduction page required by Docusaurus. Please provide your own introduction page in the `structure` option of the `docusaurus` plugin.
"""


class DocusaurusPlugin(Plugin):
    def __init__(self, input_dir: str, output_dir: str, config: Mapping[str, Any]):
        global AUTO_SIDEBAR

        super().__init__(input_dir, output_dir, config)

        os.chdir(self.input_dir)

        self.intro = False
        self.course_name = config.get("course_name", "Course")
        self.docusaurus_dir = path_utils.real_join(self.output_dir, self.course_name)
        self.init_command = [
            "npx",
            "-y",
            "create-docusaurus@2.1.0",
            self.course_name,
            "classic",
        ]
        self.docs_only = config.get("docs_only", True)

        self.build_command = ["npm", "run", "build"]

        self.sidebar = config.get("sidebar", "auto")
        self.sidebar_name = config.get("sidebar_name", "sidebar")
        AUTO_SIDEBAR = AUTO_SIDEBAR.format(sidebar=self.sidebar_name)
        self._parse_sidebar_options()

        if config.get("config_meta") is not None:
            self.config_template_args = self._parse_config_options()
        else:
            self.config_template_args = None

        if config.get("init_command") is not None:
            self.init_command = config["init_command"]

    def _parse_sidebar_options(self):
        match self.sidebar:
            case "auto":
                pass
            case "custom":
                self.sidebar_location = self.config.get(
                    "sidebar_location", f"{self.input_dir}/sidebar.js"
                )
            case "js":
                self._parse_structure()

    def _parse_config_options(self):
        config_template_args = {}

        config_template_args["docs_only"] = self.docs_only

        config_template_args["course_name"] = self.course_name
        config_template_args["logo"] = self.config.get("logo")
        config_template_args["logo_dark"] = self.config.get("logo_dark")

        config_template_args["config_meta"] = self.config.get("config_meta", {})
        config_template_args["config_socials"] = self.config.get("config_socials")
        config_template_args["categories"] = [
            list(x.keys())[0]
            for x in self.config.get("structure", {})
            if isinstance(x[list(x.keys())[0]], list)
            or isinstance(x[list(x.keys())[0]], dict)
        ]
        config_template_args["copyright_string"] = self.config.get("copyright_string")
        config_template_args["math"] = self.config.get("math", False)

        return config_template_args

    def _create_sidebar(self):
        match self.sidebar:
            case "auto":
                with open("sidebars.js", "w") as f:
                    f.write(AUTO_SIDEBAR)
            case "custom":
                # TODO copy file
                pass
            case "js":
                with open("sidebars.js", "w") as f:
                    f.write(self._render_js_sidebar())

    def _render_js_sidebar(self):
        env = Environment(
            loader=PackageLoader("openedu_builder.plugins", "docusaurus_templates")
        )
        config_template = env.get_template("sidebar.jinja2")

        sidebar_template_args = {
            "docs_only": self.docs_only,
            "sidebar_name": self.sidebar_name,
            "content": self.structure["sidebar"],
        }

        return config_template.render(**sidebar_template_args)

    def _copy_extra_files(self):
        extra_files = self.config.get("extra_files", [])
        for item in extra_files:
            if type(item) is dict:
                src = list(item.keys())[0]
                dst = item[src]
            else:
                src = item
                dst = src.split(os.path.sep)[-1]

            if os.path.isabs(dst):
                log.error(f"Destination path {dst} in extra_files cannot be absolute")
                raise PluginRunError(
                    f"Destination path {dst} in extra_files cannot be absolute"
                )

            dst = path_utils.real_join(self.docusaurus_dir, dst)

            if not os.path.isabs(src):
                src = path_utils.real_join(self.input_dir, src)

            if os.path.isdir(src):
                shutil.copytree(src, dst, dirs_exist_ok=True)
            else:
                shutil.copy(src, dst)

    def _copy_assets(self):
        static_assets = self.config.get("static_assets", [])
        for asset in static_assets:
            if type(asset) is dict:
                dst = list(asset.keys())[0]
                src = asset[dst]
            else:
                src = asset
                dst = src.split(os.path.sep)[-1]

            if os.path.isabs(dst):
                log.error(f"Destination path {dst} in static_assets cannot be absolute")
                raise PluginRunError(
                    f"Destination path {dst} in static_assets cannot be absolute"
                )

            dst = path_utils.real_join(self.docusaurus_dir, "static", dst)

            if os.path.isabs(src):
                asset_path = src
            else:
                # self.input_dir is absolute
                asset_path = path_utils.real_join(self.input_dir, src)

            if os.path.isdir(asset_path):
                shutil.copytree(asset_path, dst, dirs_exist_ok=True)
            else:
                shutil.copy(asset_path, dst)

    def _create_config(self):
        env = Environment(
            loader=PackageLoader("openedu_builder.plugins", "docusaurus_templates")
        )
        config_template = env.get_template("config.jinja2")

        with open("docusaurus.config.js", "w") as f:
            f.write(config_template.render(**self.config_template_args))

    def _organize_files(self):
        to_copy = self.structure["to_copy"]
        for src, dst in to_copy:
            if os.path.isdir(src):
                os.makedirs(dst, exist_ok=True)
                shutil.copytree(src, dst, dirs_exist_ok=True)
            else:
                os.makedirs(dst, exist_ok=True)
                shutil.copy(src, dst)

    def _create_intro(self):
        # self.output_dir is absolute
        intro_path = os.path.join(self.docusaurus_dir, "docs", "intro.md")
        if ((not self.intro) and self.sidebar == "js") and (
            not os.path.exists(intro_path)
        ):
            with open(intro_path, "w") as f:
                if self.docs_only:
                    f.write(DOCS_ONLY_FRONTMATTER)
                f.write(DUMMY_INTRO)

    def _parse_structure(self):
        structure = self.config.get("structure", {})

        to_copy = set()
        sidebar = []

        def _parse_copy(
            structure,
            src_path=os.getcwd(),
            dst_path=os.path.join(self.docusaurus_dir, "docs"),
        ):
            if isinstance(structure, list):
                for item in structure:
                    _parse_copy(item, src_path, dst_path)
            elif isinstance(structure, dict):
                for k, v in structure.items():
                    if isinstance(v, str):
                        _dst_path = os.path.join(dst_path, os.path.dirname(k))
                        to_copy.add((path_utils.real_join(src_path, v), _dst_path))
                    elif isinstance(v, dict):
                        _dst_path = os.path.join(dst_path, k)
                        _src_path = path_utils.real_join(src_path, v.get("path", ""))
                        for extra in v.get("extra", []):
                            extra = extra.rstrip(os.path.sep)
                            to_copy.add(
                                (
                                    path_utils.real_join(_src_path, extra),
                                    path_utils.real_join(
                                        _dst_path, path_utils.stem(extra)
                                    ),
                                )
                            )

                        _parse_copy(v.get("subsections", []), _src_path, _dst_path)

                    elif isinstance(v, list):
                        _dst_path = os.path.join(dst_path, k)
                        _parse_copy(v, src_path, _dst_path)
                    else:
                        raise PluginRunError(f"Invalid structure! Key: {k}, Value: {v}")
            else:
                raise PluginRunError(
                    f"This shouldn't happend! Invalid structure! Value: {structure}"
                )

        _parse_copy(structure)

        def _parse_sidebar(k, v, path=""):
            retval = {}

            # if dir := os.path.dirname(k):
            #     retval["title"] = unquote(dir)
            # else:
            retval["title"] = unquote(k.strip("/"))

            retval["id"] = path + k
            if isinstance(v, list):
                _path = f"{path}{k}/"
                retval["children"] = []
                for item in v:
                    retval["children"].append(
                        _parse_sidebar(
                            list(item.keys())[0], list(item.values())[0], _path
                        )
                    )
            elif isinstance(v, dict):
                _path = f"{path}{k}/"
                retval["children"] = []
                for item in v["subsections"]:
                    retval["children"].append(
                        _parse_sidebar(
                            list(item.keys())[0], list(item.values())[0], _path
                        )
                    )
            elif isinstance(v, str):
                if path_component := os.path.dirname(k):
                    path += f"{path_component}/"
                if stem := path_utils.stem(v):
                    id = f"{path}{stem}"
                elif stem := path_utils.stem(k):
                    id = f"{path}{stem}"
                else:
                    id = f"{path}README"

                retval["id"] = id

            return retval

        for item in structure:
            k = list(item.keys())[0]
            v = list(item.values())[0]
            if k == "Introduction":
                self.intro = True
            sidebar.append(_parse_sidebar(k, v))

        self.structure = {
            "raw": structure,
            "to_copy": to_copy,
            "sidebar": sidebar,
        }

    def _resolve_links(self):
        # Create mappings
        src_to_dst = {}
        dst_to_src = {}

        for src, dst in self.structure["to_copy"]:
            _src, _dst = src.rstrip("/"), dst.rstrip("/")
            if os.path.isfile(src) and os.path.isdir(_dst):
                _dst = os.path.join(_dst, os.path.basename(src))

            src_to_dst[_src] = _dst
            dst_to_src[_dst] = _src

        log.debug(src_to_dst)
        log.debug(dst_to_src)

        md_link_regex = re.compile(r"\[.*?\]\((\.(?:\.)?\/.*?)\)")
        iframe_link_regex = re.compile(r"<iframe.*?src=\"(\.(?:\.)?\/.*?)\".*?>")

        dst_files = []

        def _walk_struct(structure):
            if children := structure.get("children"):
                for child in children:
                    _walk_struct(child)
            else:
                dst_files.append(structure.get("id"))

        for item in self.structure["sidebar"]:
            _walk_struct(item)

        log.debug(dst_files)

        for file in dst_files:
            # Replace relative links in markdown files that have been moved,
            # using the src_to_dst and dst_to_src directory mappings.
            # File is already at the destination location. src_to_dst and dst_to_src
            # contain mappings of directories that have been moved.
            _file = os.path.join(self.docusaurus_dir, "docs", file + ".md")
            if not os.path.exists(_file):
                _file = os.path.join(self.docusaurus_dir, "docs", file + ".mdx")

            log.debug(f"Trying to open {_file}")

            if _nfile := dst_to_src.get(_file):
                src_dir = os.path.dirname(_nfile)
            else:
                src_dir = dst_to_src[os.path.dirname(_file)]
            dst_dir = os.path.dirname(_file)

            _possible_src = sorted(
                src_to_dst.keys(),
                key=lambda x: len(x.strip("/").split(os.path.sep)),
                reverse=True,
            )

            if os.path.exists(_file):
                with open(_file, "r") as f:
                    content = f.read()

                for match in itertools.chain(
                    re.finditer(md_link_regex, content),
                    re.finditer(iframe_link_regex, content),
                ):
                    src_link = match.group(1)
                    log.info(f"Found link {src_link}")
                    # dst_link = src_to_dst[path_utils.real_join(src_dir, src_link)]
                    # dst_link = os.path.relpath(dst_dir, dst_link)
                    src_ref = path_utils.real_join(src_dir, src_link)
                    log.debug(f"Link {src_link} refers to {src_ref}")
                    try:
                        _src_ref = next(
                            x for x in _possible_src if src_ref.startswith(x)
                        )
                        src_ref_dir = (
                            os.path.dirname(_src_ref)
                            if os.path.isfile(_src_ref)
                            else _src_ref
                        )
                        log.debug(f"File {src_ref} found under {src_ref_dir}")
                    except StopIteration:
                        log.warning(
                            f"Couldn't find {src_ref} in source files. Perhaps you didn't copy it? Skipping link {src_link}."
                        )
                        continue

                    _dst_ref = src_to_dst[_src_ref]
                    dst_ref_dir = (
                        os.path.dirname(_dst_ref)
                        if os.path.isfile(_dst_ref)
                        else _dst_ref
                    )
                    log.debug(
                        f"File {src_ref} should be copied under {dst_ref_dir} at the destination."
                    )
                    dst_ref = path_utils.real_join(
                        dst_ref_dir, src_ref.removeprefix(src_ref_dir).lstrip("/")
                    )
                    log.debug(
                        f"File {src_ref} should be copied to {dst_ref} at the destination."
                    )
                    dst_link = os.path.relpath(dst_ref, dst_dir)
                    if " " in dst_link:
                        dst_link = f"<{dst_link}>"
                        # Temporary fix until https://github.com/facebook/docusaurus/issues/8867 gets fixed
                        dst_link = dst_link.replace(".md", "")
                    log.info(f"New link should be {dst_link}")

                    content = content.replace(src_link, dst_link)

                with open(_file, "w") as f:
                    f.write(content)

    def run(self):
        if self.config.get("structure") is None and self.sidebar == "js":
            raise PluginRunError(
                "structure option is required for this plugin when using js sidebar"
            )

        self._parse_structure()

        log.debug(self.structure["raw"])
        log.debug(self.structure["to_copy"])
        log.debug(self.structure["sidebar"])

        # Run init command
        os.chdir(self.output_dir)
        p = subprocess.run(self.init_command, capture_output=True)
        if p.returncode != 0:
            log.error(f"Command {self.init_command} failed with code {p.returncode}")
            log.error(f"STDOUT: {p.stdout.decode('utf-8')}")
            log.error(f"STDERR: {p.stderr.decode('utf-8')}")
            raise PluginRunError("Error while running init command")

        # Folders we need to delete:
        # - blog
        try:
            shutil.rmtree(path_utils.real_join(self.docusaurus_dir, "blog"))
        except FileNotFoundError:
            log.warn("Blog folder already removed")
        # - delete and recreate docs
        try:
            shutil.rmtree(path_utils.real_join(self.docusaurus_dir, "docs"))
        except FileNotFoundError:
            log.warn("Docs folder already removed")

        os.mkdir(path_utils.real_join(self.docusaurus_dir, "docs"))

        os.chdir(self.docusaurus_dir)
        # Files we need to edit:
        # - docusaurus.config.js
        if self.config_template_args is not None:
            self._create_config()
        # - sidebars.js
        self._create_sidebar()

        if self.config.get("structure") is not None:
            # Copy or link documentation in the right place
            # self._parse_structure()
            self._organize_files()
            self._resolve_links()

        self._copy_extra_files()

        # Copy extra static assets and files
        self._copy_assets()

        # Create dummy intro if user did not provide one
        self._create_intro()

        if self.docs_only:
            try:
                os.remove("src/pages/index.js")
            except FileNotFoundError:
                log.info("index.js already removed")

        if self.config.get("math", False):
            math_command = [
                "npm",
                "install",
                "--save",
                "remark-math@3",
                "rehype-katex@5",
                "hast-util-is-element@1.1.0",
            ]
            p = subprocess.run(math_command, capture_output=True)
            if p.returncode != 0:
                log.error(f"Command {math_command} failed with code {p.returncode}")
                log.error(f"STDOUT: {p.stdout.decode('utf-8')}")
                log.error(f"STDERR: {p.stderr.decode('utf-8')}")
                raise PluginRunError("Error while installing math dependencies command")

        p = subprocess.run(self.build_command, capture_output=True)
        if p.returncode != 0:
            log.error(f"Command {self.build_command} failed with code {p.returncode}")
            log.error(f"STDOUT: {p.stdout.decode('utf-8')}")
            log.error(f"STDERR: {p.stderr.decode('utf-8')}")
            raise PluginRunError("Error while running build command")

        # os.mkdir(path_utils.real_join(self.output_dir, "output"))
        if not self.config.get("debug", False):
            tmp_dir = tempfile.mkdtemp()
            shutil.copytree(
                path_utils.real_join(self.docusaurus_dir, "build"),
                tmp_dir,
                dirs_exist_ok=True,
            )

            shutil.rmtree(self.docusaurus_dir)
            shutil.copytree(tmp_dir, self.output_dir, dirs_exist_ok=True)
