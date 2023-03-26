from conan import ConanFile
from conan.errors import ConanException, ConanInvalidConfiguration
from conan.tools.apple import fix_apple_shared_install_name, is_apple_os
from conan.tools.build import cross_building
from conan.tools.env import VirtualBuildEnv, VirtualRunEnv
from conan.tools.files import apply_conandata_patches, chdir, collect_libs, copy, export_conandata_patches, get, replace_in_file, rmdir
from conan.tools.gnu import Autotools, AutotoolsDeps, AutotoolsToolchain, PkgConfigDeps
from conan.tools.microsoft import is_msvc, is_msvc_static_runtime, msvc_runtime_flag, NMakeToolchain, unix_path
from conan.tools.scm import Version
import os

required_conan_version = ">=1.54.0"


class TkConan(ConanFile):
    name = "tk"
    description = "Tk is a graphical user interface toolkit that takes developing desktop applications to a higher level than conventional approaches."
    license = "TCL"
    url = "https://github.com/conan-io/conan-center-index"
    homepage = "https://tcl.tk"
    topics = ("conan", "tk", "gui", "tcl", "scripting", "programming")
    package_type = "library"
    settings = "os", "arch", "compiler", "build_type"
    options = {
        "shared": [True, False],
        "fPIC": [True, False],
    }
    default_options = {
        "shared": False,
        "fPIC": True,
    }

    @property
    def _settings_build(self):
        return getattr(self, "settings_build", self.settings)

    def export_sources(self):
        export_conandata_patches(self)

    def config_options(self):
        if self.settings.os == "Windows":
            del self.options.fPIC

    def configure(self):
        if self.options.shared:
            self.options.rm_safe("fPIC")
        self.settings.rm_safe("compiler.libcxx")
        self.settings.rm_safe("compiler.cppstd")

    def layout(self):
        # Not using basic_layout because package() needs the source folder to be a sub-directory of the build folder
        self.folders.source = "src"
        self.folders.generators = "conan"

    def requirements(self):
        self.requires("tcl/{}".format(self.version))
        if self.settings.os == "Linux":
            self.requires("fontconfig/2.13.93")
            self.requires("xorg/system")

    def build_requirements(self):
        if self._settings_build.os == "Windows" and not is_msvc(self):
            self.win_bash = True
            if not self.conf.get("tools.microsoft.bash:path", check_type=str):
                self.tool_requires("msys2/cci.latest")

    def validate(self):
        if self.options["tcl"].shared != self.options.shared:
            raise ConanInvalidConfiguration("The shared option of tcl and tk must have the same value")

    def source(self):
        get(self, **self.conan_data["sources"][self.version], strip_root=True)

    def generate(self):
        # inject tool_requires env vars in build scope (not needed if there is no tool_requires)
        env = VirtualBuildEnv(self)
        env.generate()
        # inject requires env vars in build scope
        # it's required in case of native build when there is AutotoolsDeps & at least one dependency which might be shared, because configure tries to run a test executable
        if not cross_building(self):
            env = VirtualRunEnv(self)
            env.generate(scope="build")

        if is_msvc(self):
            tc = NMakeToolchain(self)
            tc.generate()
        else:
            tcl_root = self.deps_cpp_info["tcl"].rootpath
            tclConfigShFolder = os.path.join(tcl_root, "lib").replace("\\", "/")

            tc = AutotoolsToolchain(self, prefix=self.package_folder)
            def yes_no(v): return "yes" if v else "no"
            tc.configure_args.extend([
                "--with-tcl={}".format(unix_path(tclConfigShFolder)),
                "--enable-threads",
                "--enable-symbols={}".format(yes_no(self.settings.build_type == "Debug")),
                "--enable-64bit={}".format(yes_no(self.settings.arch == "x86_64")),
                "--with-x={}".format(yes_no(self.settings.os == "Linux")),
                "--enable-aqua={}".format(yes_no(is_apple_os(self.settings.os))),
            ])
            if self.settings.os == "Windows":
                tc.defines.extend(["UNICODE", "_UNICODE", "_ATL_XP_TARGETING", ])
            tc.generate()
            # generate pkg-config files of dependencies (useless if upstream configure.ac doesn't rely on PKG_CHECK_MODULES macro)
            tc = PkgConfigDeps(self)
            tc.generate()
            tc = AutotoolsDeps(self)
            tc.generate()

    def _get_default_build_system_subdir(self):
        return {
            "Macos": "macosx",
            "FreeBSD": "unix",
            "Linux": "unix",
            "Windows": "win",
        }[str(self.settings.os)]

    def _get_configure_dir(self, build_system_subdir=None):
        if build_system_subdir is None:
            build_system_subdir = self._get_default_build_system_subdir()
        return os.path.join(self.source_folder, build_system_subdir)

    def _patch_sources(self):
        apply_conandata_patches(self)

        for build_system in ("unix", "win", ):
            config_dir = self._get_configure_folder(build_system)

            if build_system != "win":
                # When disabling 64-bit support (in 32-bit), this test must be 0 in order to use "long long" for 64-bit ints
                # (${tcl_type_64bit} can be either "__int64" or "long long")
                replace_in_file(self, os.path.join(config_dir, "configure"),
                                "(sizeof(${tcl_type_64bit})==sizeof(long))",
                                "(sizeof(${tcl_type_64bit})!=sizeof(long))")

            makefile_in = os.path.join(config_dir, "Makefile.in")
            # Avoid clearing CFLAGS and LDFLAGS in the makefile
            # replace_in_file(self, makefile_in, "\nCFLAGS{}".format(" " if (build_system == "win" and name == "tcl") else "\t"), "\n#CFLAGS\t")
            replace_in_file(self, makefile_in, "\nLDFLAGS\t", "\n#LDFLAGS\t")
            replace_in_file(self, makefile_in, "${CFLAGS}", "${CFLAGS} ${CPPFLAGS}")

        rules_ext_vc = os.path.join(self.source_folder, self._source_subfolder, "win", "rules-ext.vc")
        replace_in_file(self, rules_ext_vc,
                        "\n_RULESDIR = ",
                        "\n_RULESDIR = .\n#_RULESDIR = ")
        rules_vc = os.path.join(self.source_folder, self._source_subfolder, "win", "rules.vc")
        replace_in_file(self, rules_vc,
                        r"$(_TCLDIR)\generic",
                        r"$(_TCLDIR)\include")
        replace_in_file(self, rules_vc,
                        "\nTCLSTUBLIB",
                        "\n#TCLSTUBLIB")
        replace_in_file(self, rules_vc,
                        "\nTCLIMPLIB",
                        "\n#TCLIMPLIB")

        win_makefile_in = os.path.join(self._get_configure_folder("win"), "Makefile.in")
        replace_in_file(self, win_makefile_in, "\nTCL_GENERIC_DIR", "\n#TCL_GENERIC_DIR")

        win_rules_vc = os.path.join(self._source_subfolder, "win", "rules.vc")
        replace_in_file(self, win_rules_vc,
                        "\ncwarn = $(cwarn) -WX",
                        "\n# cwarn = $(cwarn) -WX")
        # disable whole program optimization to be portable across different MSVC versions.
        # See conan-io/conan-center-index#4811 conan-io/conan-center-index#4094
        replace_in_file(self,
                        win_rules_vc,
                        "OPTIMIZATIONS  = $(OPTIMIZATIONS) -GL",
                        "# OPTIMIZATIONS  = $(OPTIMIZATIONS) -GL")

    def _build_nmake(self, target):
        # https://core.tcl.tk/tips/doc/trunk/tip/477.md
        opts = []
        if not self.options.shared:
            opts.append("static")
        if self.settings.build_type == "Debug":
            opts.append("symbols")
        if is_msvc_static_runtime(self):
            opts.append("nomsvcrt")
        else:
            opts.append("msvcrt")
        if "d" not in msvc_runtime_flag(self):
            opts.append("unchecked")
        # https://core.tcl.tk/tk/tktview?name=3d34589aa0
        # https://wiki.tcl-lang.org/page/Building+with+Visual+Studio+2017
        tcl_lib_path = os.path.join(self.deps_cpp_info["tcl"].rootpath, "lib")
        tclimplib, tclstublib = None, None
        for lib in os.listdir(tcl_lib_path):
            if not lib.endswith(".lib"):
                continue
            if lib.startswith("tcl{}".format("".join(self.version.split(".")[:2]))):
                tclimplib = os.path.join(tcl_lib_path, lib)
            elif lib.startswith("tclstub{}".format("".join(self.version.split(".")[:2]))):
                tclstublib = os.path.join(tcl_lib_path, lib)

        if tclimplib is None or tclstublib is None:
            raise ConanException("tcl dependency misses tcl and/or tclstub library")

        tcldir = self.deps_cpp_info["tcl"].rootpath.replace("/", "\\\\")
        self.run(
            """nmake -nologo -f "{cfgdir}/makefile.vc" INSTALLDIR="{pkgdir}" OPTS={opts} TCLDIR="{tcldir}" TCL_LIBRARY="{tcl_library}" TCLIMPLIB="{tclimplib}" TCLSTUBLIB="{tclstublib}" {target}""".format(
                cfgdir=self._get_configure_dir("win"),
                pkgdir=self.package_folder,
                opts=",".join(opts),
                tcldir=tcldir,
                tclstublib=tclstublib,
                tclimplib=tclimplib,
                tcl_library=self.deps_env_info['tcl'].TCL_LIBRARY.replace("\\", "/"),
                target=target,
            ), cwd=self._get_configure_folder("win"),
        )

    def _get_autotools_args(self):
        tcl_root = self.deps_cpp_info["tcl"].rootpath
        return ["TCL_GENERIC_DIR={}".format(os.path.join(tcl_root, "include")).replace("\\", "/")]

    def build(self):
        self._patch_sources()
        if self.settings.compiler == "Visual Studio":
            self._build_nmake("release")
        else:
            autotools = Autotools(self)
            autotools.configure(self._get_configure_dir())
            autotools.make(args=self._get_autotools_args())

    def package(self):
        self.copy(pattern="license.terms", src=self._source_subfolder, dst="licenses")
        if self.settings.compiler == "Visual Studio":
            self._build_nmake("install")
        else:
            autotools = Autotools(self)
            autotools.make(target="install", args=self._get_autotools_args())
            autotools.make(target="install-private-headers", args=self._get_autotools_args())
            rmdir(self, os.path.join(self.package_folder, "lib", "pkgconfig"))
            rmdir(self, os.path.join(self.package_folder, "man"))
            rmdir(self, os.path.join(self.package_folder, "share"))

        # FIXME: move to patch
        tkConfigShPath = os.path.join(self.package_folder, "lib", "tkConfig.sh")
        if os.path.exists(tkConfigShPath):
            pkg_path = os.path.join(self.package_folder).replace('\\', '/')
            replace_in_file(self, tkConfigShPath,
                            pkg_path,
                            "${TK_ROOT}")
            replace_in_file(self, tkConfigShPath,
                            "\nTK_BUILD_",
                            "\n#TK_BUILD_")
            replace_in_file(self, tkConfigShPath,
                            "\nTK_SRC_DIR",
                            "\n#TK_SRC_DIR")

        fix_apple_shared_install_name(self)

    def package_info(self):
        if self.settings.compiler == "Visual Studio":
            tk_version = Version(self.version)
            lib_infix = "{}{}".format(tk_version.major, tk_version.minor)
            tk_suffix = "t{}{}{}".format(
                "" if self.options.shared else "s",
                "g" if self.settings.build_type == "Debug" else "",
                "x" if "MD" in str(self.settings.compiler.runtime) and not self.options.shared else "",
            )
        else:
            tk_version = Version(self.version)
            lib_infix = "{}.{}".format(tk_version.major, tk_version.minor)
            tk_suffix = ""
        self.cpp_info.libs = ["tk{}{}".format(lib_infix, tk_suffix), "tkstub{}".format(lib_infix)]
        if self.settings.os == "Macos":
            self.cpp_info.frameworks = ["CoreFoundation", "Cocoa", "Carbon", "IOKit"]
        elif self.settings.os == "Windows":
            self.cpp_info.system_libs = [
                "netapi32", "kernel32", "user32", "advapi32", "userenv", "ws2_32", "gdi32",
                "comdlg32", "imm32", "comctl32", "shell32", "uuid", "ole32", "oleaut32"
            ]

        tk_library = os.path.join(self.package_folder, "lib", "{}{}".format(self.name, ".".join(self.version.split(".")[:2]))).replace("\\", "/")
        self.output.info("Setting TK_LIBRARY environment variable: {}".format(tk_library))
        self.runenv_info.define_path('TK_LIBRARY', tk_library)
        self.env_info.TK_LIBRARY = tk_library

        tcl_root = self.package_folder.replace("\\", "/")
        self.output.info("Setting TCL_ROOT environment variable: {}".format(tcl_root))
        self.runenv_info.define_path('TCL_ROOT', tcl_root)
        self.env_info.TCL_ROOT = tcl_root
