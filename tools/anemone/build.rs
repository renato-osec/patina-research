// Match exoskeleton: tell rustc to link directly against libbinaryninjacore.so.1
// at runtime (not the symlink), and embed an rpath so the wheel works.
use std::env;

fn main() {
    let binja_path = env::var("BINJA_PATH")
        .expect("BINJA_PATH must be set to the Binary Ninja install dir");
    println!("cargo:rustc-link-search=native={}", binja_path);
    println!("cargo:rustc-link-lib=dylib:+verbatim=libbinaryninjacore.so.1");
    println!("cargo:rustc-link-arg=-Wl,-rpath,{}", binja_path);
    println!("cargo:rerun-if-env-changed=BINJA_PATH");
}
