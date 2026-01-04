import argparse
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

#
# config
#

SLANG_REPO = "https://github.com/shader-slang/slang.git"
DEFAULT_TAG = "v2025.24.2"
ANDROID_ABI = "arm64-v8a"
ANDROID_PLATFORM = "android-30"
DIST_DIR = Path("dist").absolute()
BUILD_BASE = Path("build_slang").absolute()


def find_ndk():
    for var in ["ANDROID_NDK_HOME", "ANDROID_NDK_ROOT", "NDK_HOME"]:
        path = os.environ.get(var)
        if path and os.path.exists(path):
            return Path(path)

    sdk_roots = []
    for var in ["ANDROID_HOME", "ANDROID_SDK_ROOT"]:
        path = os.environ.get(var)
        if path:
            sdk_roots.append(Path(path))

    if platform.system() == "Windows":
        sdk_roots.append(Path(os.environ.get("LOCALAPPDATA", "")) / "Android/Sdk")
        sdk_roots.append(Path("C:/Program Files (x86)/Android/android-sdk"))
    else:
        sdk_roots.append(Path.home() / "Android/Sdk")
        sdk_roots.append(Path.home() / "Library/Android/sdk")

    for root in sdk_roots:
        if not root.exists():
            continue

        ndk_dir = root / "ndk"
        if ndk_dir.exists():
            versions = sorted(
                [d for d in ndk_dir.iterdir() if d.is_dir()], reverse=True
            )
            if versions:
                return versions[0]

        bundle_dir = root / "ndk-bundle"
        if bundle_dir.exists():
            return bundle_dir

    return None


def run_cmd(cmd, cwd=None, env=None):
    print(f"\n> Executing: {' '.join(cmd)}")
    print(f"> in: {cwd if cwd else os.getcwd()}\n")
    full_env = os.environ.copy()
    if env:
        full_env.update(env)

    subprocess.run(cmd, cwd=cwd, env=full_env, check=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--tag",
        help="Slang git tag to build",
        default=os.environ.get("SLANG_TAG", DEFAULT_TAG),
    )
    args, unknown = parser.parse_known_args()
    slang_tag = args.tag

    ndk_path = find_ndk()
    if not ndk_path:
        print(
            "Error: Could not find Android NDK. Please set ANDROID_NDK_HOME environment variable."
        )
        sys.exit(1)

    print(f"--- Detected NDK: {ndk_path}")
    print(f"--- Target ABI: {ANDROID_ABI}")
    print(f"--- Target Platform: {ANDROID_PLATFORM}")

    if not BUILD_BASE.exists():
        BUILD_BASE.mkdir()

    slang_dir = BUILD_BASE / "slang"
    host_build_dir = BUILD_BASE / "build-host"
    android_build_dir = BUILD_BASE / "build-android"

    if DIST_DIR.exists():
        shutil.rmtree(DIST_DIR)
    DIST_DIR.mkdir()

    # clone slang
    if not slang_dir.exists():
        print(f"--- Cloning Slang {slang_tag}...")
        run_cmd(
            [
                "git",
                "clone",
                "--recursive",
                "--branch",
                slang_tag,
                SLANG_REPO,
                str(slang_dir),
            ]
        )
    else:
        print("--- Slang directory exists, skipping clone.")

    common_flags = [
        "-DSLANG_ENABLE_GFX=OFF",
        "-DSLANG_ENABLE_SLANG_RHI=OFF",
        "-DSLANG_ENABLE_SLANGRT=OFF",
        "-DSLANG_ENABLE_EXAMPLES=OFF",
        "-DSLANG_ENABLE_TESTS=OFF",
    ]

    # build host generators
    print("--- Building Host Generators...")
    if not host_build_dir.exists():
        host_build_dir.mkdir()

    cmake_host_cmd = [
        "cmake",
        "-S",
        str(slang_dir),
        "-B",
        str(host_build_dir),
        "-GNinja",
        "-DCMAKE_BUILD_TYPE=Release",
        "-DSLANG_BUILD_GENERATORS=ON",
        "-DSLANG_LIB_TYPE=STATIC",
    ] + common_flags
    run_cmd(cmake_host_cmd)
    run_cmd(["cmake", "--build", str(host_build_dir)])

    host_tools_dir = BUILD_BASE / "host-tools"
    run_cmd(
        [
            "cmake",
            "--install",
            str(host_build_dir),
            "--prefix",
            str(host_tools_dir),
            "--component",
            "generators",
        ]
    )

    # build slang
    print("--- Building Slang for Android...")
    if not android_build_dir.exists():
        android_build_dir.mkdir()

    toolchain_file = ndk_path / "build/cmake/android.toolchain.cmake"
    if not toolchain_file.exists():
        print(f"Error: Toolchain file not found at {toolchain_file}")
        sys.exit(1)

    cmake_android_cmd = [
        "cmake",
        "-S",
        str(slang_dir),
        "-B",
        str(android_build_dir),
        "-GNinja",
        f"-DCMAKE_TOOLCHAIN_FILE={toolchain_file}",
        f"-DANDROID_ABI={ANDROID_ABI}",
        f"-DANDROID_PLATFORM={ANDROID_PLATFORM}",
        "-DCMAKE_BUILD_TYPE=Release",
        "-DSLANG_LIB_TYPE=SHARED",
        f"-DSLANG_GENERATORS_PATH={host_tools_dir / 'bin'}",
        "-DSLANG_SLANG_LLVM_FLAVOR=DISABLE",
    ] + common_flags
    run_cmd(cmake_android_cmd)
    run_cmd(["cmake", "--build", str(android_build_dir), "--target", "slang"])

    # copy artifacts
    print(f"--- Collecting binaries to {DIST_DIR}...")

    lib_extensions = [".so", ".a"]
    search_paths = [
        android_build_dir / "lib",
        android_build_dir / "bin",
        android_build_dir / "Release" / "lib",
        android_build_dir / "Release" / "bin",
    ]

    found = False
    for spath in search_paths:
        if not spath.exists():
            continue
        for item in spath.iterdir():
            if "lib" in item.name and any(
                item.name.endswith(ext) for ext in lib_extensions
            ):
                shutil.copy(item, DIST_DIR / item.name)
                print(f"Copied: {item.name}")
                found = True

    inc_dist = DIST_DIR / "include"
    if inc_dist.exists():
        shutil.rmtree(inc_dist)
    shutil.copytree(slang_dir / "include", inc_dist)
    print("Copied include directory.")

    if found:
        print("\n--- Success! Check the 'dist' folder.")
    else:
        print("\n--- Failed to find libslang.so. Check build logs.")


if __name__ == "__main__":
    if "--help" in sys.argv or "-h" in sys.argv:
        print("Usage: python build.py")
        sys.exit(0)
    main()
