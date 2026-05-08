fn main() {
    let out = std::process::Command::new("rustc")
        .args(["--print", "sysroot"])
        .output()
        .expect("failed to get sysroot");
    let sysroot = String::from_utf8(out.stdout).unwrap();
    println!("cargo:rustc-link-search=native={}/lib", sysroot.trim());

    // `rustc -V --verbose` -> NACRE_RUSTC_VERSION for patina's layout
    // alignment check against chela's extracted binary version.
    let vv = std::process::Command::new("rustc").arg("-vV").output().unwrap();
    let s = String::from_utf8_lossy(&vv.stdout);
    let release = s
        .lines()
        .find_map(|l| l.strip_prefix("release: "))
        .unwrap_or("unknown");
    println!("cargo:rustc-env=NACRE_RUSTC_VERSION={release}");
}
