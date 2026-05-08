fn main() {
    let out = std::process::Command::new("rustc")
        .args(["--print", "sysroot"])
        .output()
        .expect("failed to get sysroot");
    let sysroot = String::from_utf8(out.stdout).unwrap();
    println!("cargo:rustc-link-search=native={}/lib", sysroot.trim());
}
