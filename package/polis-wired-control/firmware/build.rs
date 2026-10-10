fn main() {
    assert_eq!(
        std::env::var("TARGET").unwrap(),
        "thumbv8m.main-none-eabihf"
    );
    println!("cargo:rerun-if-changed=memory.x");
    let out = std::path::PathBuf::from(std::env::var_os("OUT_DIR").unwrap());
    std::fs::copy("memory.x", out.join("memory.x")).unwrap();
    println!("cargo:rustc-link-search={}", out.display());
    for arg in ["--nmagic", "-Tlink.x", "-Tdefmt.x"] {
        println!("cargo:rustc-link-arg-bin=polis-wired-firmware={arg}");
    }
}
