#![feature(rustc_private)]

// fetches crates

use std::collections::HashMap;
use std::path::{Path, PathBuf};

use chela::Crate;

pub use nacre::StructLayout;

/// Rustc release nacre was built against.
pub fn nacre_rustc_version() -> &'static str {
    nacre::RUSTC_VERSION
}

#[derive(Debug, Clone)]
pub struct CrateSource {
    pub krate: Crate,
    pub src_path: PathBuf,
}

#[derive(thiserror::Error, Debug)]
pub enum CarpaceError {
    #[error("io: {0}")]
    Io(#[from] std::io::Error),
    #[error("crate not found in cargo cache: {name}-{version}")]
    NotCached { name: String, version: String },
    #[error("cargo fetch failed: {0}")]
    Fetch(String),
    #[error("nacre: {0}")]
    Nacre(String),
}

/// Look for `<name>-<version>/` under `$CARGO_HOME/registry/src/*/`.
pub fn find_cached(krate: &Crate) -> Option<CrateSource> {
    let cargo_home = std::env::var("CARGO_HOME").map(PathBuf::from).ok()
        .or_else(|| std::env::var("HOME").ok().map(|h| PathBuf::from(h).join(".cargo")))?;
    let registry_src = cargo_home.join("registry").join("src");
    let entries = std::fs::read_dir(&registry_src).ok()?;
    let want = format!("{}-{}", krate.name, krate.version);
    for ent in entries.flatten() {
        let candidate = ent.path().join(&want);
        if candidate.is_dir() {
            return Some(CrateSource {
                krate: krate.clone(),
                src_path: candidate,
            });
        }
    }
    None
}

/// Throwaway `cargo fetch` for `krate`, returning its cache path.
pub fn fetch(krate: &Crate) -> Result<CrateSource, CarpaceError> {
    if let Some(s) = find_cached(krate) {
        return Ok(s);
    }
    let dir = std::env::temp_dir().join(format!(
        "carpace_fetch_{}_{}_{}",
        krate.name,
        krate.version,
        std::process::id()
    ));
    std::fs::create_dir_all(&dir)?;
    std::fs::write(
        dir.join("Cargo.toml"),
        format!(
            "[package]\nname = \"_carpace_stub\"\nversion = \"0.0.1\"\nedition = \"2021\"\n\n\
             [dependencies]\n{} = \"={}\"\n",
            krate.name, krate.version
        ),
    )?;
    std::fs::create_dir_all(dir.join("src"))?;
    std::fs::write(dir.join("src").join("lib.rs"), "")?;

    let status = std::process::Command::new("cargo")
        .arg("fetch")
        .current_dir(&dir)
        .status()
        .map_err(|e| CarpaceError::Fetch(e.to_string()))?;
    let _ = std::fs::remove_dir_all(&dir);
    if !status.success() {
        return Err(CarpaceError::Fetch(format!("exit {status:?}")));
    }
    find_cached(krate).ok_or_else(|| CarpaceError::NotCached {
        name: krate.name.clone(),
        version: krate.version.clone(),
    })
}

/// Build a stub cargo project against `crates`, returning `target/release/deps`.
pub fn build_stub_deps(crates: &[Crate]) -> Result<PathBuf, CarpaceError> {
    let dir = std::env::temp_dir().join(format!("carpace_stub_build_{}", std::process::id()));
    std::fs::create_dir_all(&dir)?;

    const SYSROOT_CRATES: &[&str] = &[
        "std", "core", "alloc", "proc_macro", "test",
        "compiler_builtins", "panic_abort", "panic_unwind",
        "unwind", "rustc_std_workspace_core",
        "rustc_std_workspace_alloc", "rustc_std_workspace_std",
    ];
    let drop_set: std::collections::BTreeSet<String> = std::env::var("CARPACE_STUB_DROP")
        .unwrap_or_default()
        .split(',')
        .map(|s| s.trim().to_string())
        .filter(|s| !s.is_empty())
        .collect();

    let override_specs: std::collections::BTreeMap<String, String> = std::env::var(
        "CARPACE_STUB_OVERRIDES_FILE",
    )
    .ok()
    .and_then(|p| std::fs::read_to_string(&p).ok())
    .map(|s| parse_override_specs(&s))
    .unwrap_or_default();

    let mut by_name: std::collections::BTreeMap<&str, &str> = Default::default();
    for k in crates {
        if SYSROOT_CRATES.contains(&k.name.as_str()) {
            continue;
        }
        if drop_set.contains(k.name.as_str()) {
            continue;
        }
        by_name.entry(k.name.as_str()).or_insert(k.version.as_str());
    }
    let mut toml = String::from(
        "[package]\nname = \"_carpace_stub\"\nversion = \"0.0.1\"\nedition = \"2021\"\n\n[dependencies]\n",
    );
    let exact_pins = std::env::var("CARPACE_STUB_EXACT_PINS").is_ok();
    let mut emitted: std::collections::BTreeSet<String> = Default::default();
    for (name, version) in &by_name {
        if let Some(spec) = override_specs.get(*name) {
            toml.push_str(&format!("{name} = {spec}\n"));
        } else {
            let op = if exact_pins { "=" } else { "^" };
            toml.push_str(&format!("{name} = \"{op}{version}\"\n"));
        }
        emitted.insert((*name).to_string());
    }
    // Overrides not in chela's detected list still get added (extras).
    for (name, spec) in &override_specs {
        if !emitted.contains(name) && !drop_set.contains(name) {
            toml.push_str(&format!("{name} = {spec}\n"));
        }
    }

    if let Ok(extra) = std::env::var("CARPACE_STUB_APPEND_FILE") {
        if let Ok(s) = std::fs::read_to_string(&extra) {
            toml.push_str("\n");
            toml.push_str(&s);
        }
    }

    std::fs::write(dir.join("Cargo.toml"), toml)?;
    std::fs::create_dir_all(dir.join("src"))?;
    std::fs::write(dir.join("src").join("lib.rs"), "")?;
    let stub_channel = std::env::var("CARPACE_STUB_CHANNEL")
        .unwrap_or_else(|_| "nightly".to_string());
    std::fs::write(
        dir.join("rust-toolchain.toml"),
        format!("[toolchain]\nchannel = \"{stub_channel}\"\n"),
    )?;

    let t = std::time::Instant::now();
    eprintln!(
        "carpace: building stub with {} crates @ {}...",
        by_name.len(),
        dir.display()
    );
    let status = std::process::Command::new("cargo")
        .args(["build", "--release"])
        .current_dir(&dir)
        .status()
        .map_err(|e| CarpaceError::Fetch(e.to_string()))?;
    if !status.success() {
        return Err(CarpaceError::Fetch(format!("stub build exit {status:?}")));
    }
    eprintln!("carpace: stub built in {:.1}s", t.elapsed().as_secs_f64());
    Ok(dir.join("target/release/deps"))
}

fn parse_override_specs(raw: &str) -> std::collections::BTreeMap<String, String> {
    let mut out = std::collections::BTreeMap::new();
    for line in raw.lines() {
        let line = line.trim();
        if line.is_empty() || line.starts_with('#') {
            continue;
        }
        let Some((name, spec)) = line.split_once('=') else { continue };
        out.insert(name.trim().to_string(), spec.trim().to_string());
    }
    out
}

/// Returns `(found, missing)`. `auto_fetch` triggers `cargo fetch`.
pub fn resolve_all(
    crates: &[Crate],
    auto_fetch: bool,
) -> (Vec<CrateSource>, Vec<Crate>) {
    let mut found = Vec::new();
    let mut missing = Vec::new();
    for k in crates {
        let src = if auto_fetch {
            fetch(k).ok()
        } else {
            find_cached(k)
        };
        match src {
            Some(s) => found.push(s),
            None => missing.push(k.clone()),
        }
    }
    (found, missing)
}

/// Map `crate_name -> rlib` under `target_deps`.
pub fn locate_rlibs(
    sources: &[CrateSource],
    target_deps: &Path,
) -> HashMap<String, PathBuf> {
    let mut out = HashMap::new();
    let Ok(entries) = std::fs::read_dir(target_deps) else {
        return out;
    };
    let files: Vec<_> = entries.flatten().map(|e| e.path()).collect();

    for src in sources {
        let expect = src.krate.name.replace('-', "_");
        let prefix = format!("lib{}-", expect);
        let hit = files.iter().find(|p| {
            p.file_name()
                .and_then(|s| s.to_str())
                .map(|n| n.starts_with(&prefix) && n.ends_with(".rlib"))
                .unwrap_or(false)
        });
        if let Some(p) = hit {
            out.insert(expect, p.clone());
        }
    }
    out
}

/// Enumerate every `lib<name>-<hash>.rlib` under `target_deps`.
pub fn locate_all_rlibs(target_deps: &Path) -> HashMap<String, PathBuf> {
    let mut out = HashMap::new();
    let Ok(entries) = std::fs::read_dir(target_deps) else {
        return out;
    };
    for e in entries.flatten() {
        let p = e.path();
        let Some(fname) = p.file_name().and_then(|s| s.to_str()) else { continue };
        if !(fname.starts_with("lib") && fname.ends_with(".rlib")) {
            continue;
        }
        let stem = &fname[3..fname.len() - 5];
        let name = match stem.rfind('-') {
            Some(i) => &stem[..i],
            None => stem,
        };
        out.entry(name.to_string()).or_insert(p.clone());
    }
    out
}

/// Layouts for every pub ADT across all rlibs in `target_deps`.
pub fn probe_layouts(
    _sources: &[CrateSource],
    target_deps: &Path,
) -> Result<Vec<StructLayout>, CarpaceError> {
    let rlibs = locate_all_rlibs(target_deps);
    let externs: Vec<(String, String)> = rlibs
        .iter()
        .map(|(n, p)| (n.clone(), p.to_string_lossy().into_owned()))
        .collect();
    let extra = vec![
        "-L".to_string(),
        format!("dependency={}", target_deps.display()),
    ];
    nacre::dep_catalog(None, &externs, &extra).map_err(CarpaceError::Nacre)
}

#[cfg(feature = "python")]
mod py {
    use pyo3::prelude::*;

    use chela::Crate;

    use crate::{build_stub_deps, nacre_rustc_version, probe_layouts, resolve_all};

    /// Build a stub crate with given dep pairs; returns `target/release/deps`.
    #[pyfunction]
    fn build_stub(crates: Vec<(String, String)>) -> PyResult<String> {
        let ks: Vec<Crate> = crates
            .into_iter()
            .map(|(name, version)| Crate { name, version })
            .collect();
        build_stub_deps(&ks)
            .map(|p| p.to_string_lossy().into_owned())
            .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))
    }

    /// Run `nacre::dep_catalog` against every `.rlib` in `target_deps`.
    #[pyfunction]
    fn probe(
        target_deps: String,
    ) -> PyResult<Vec<(String, u64, u64, Vec<(String, u64, u64, String)>)>> {
        let layouts =
            probe_layouts(&[], std::path::Path::new(&target_deps)).map_err(|e| {
                pyo3::exceptions::PyRuntimeError::new_err(e.to_string())
            })?;
        Ok(layouts
            .into_iter()
            .map(|sl| {
                (
                    sl.name,
                    sl.size,
                    sl.align,
                    sl.flat
                        .into_iter()
                        .map(|f| (f.path, f.offset, f.size, f.ty_desc))
                        .collect(),
                )
            })
            .collect())
    }

    /// Report cache status for each `(name, version)` pair.
    #[pyfunction]
    fn resolve(
        crates: Vec<(String, String)>,
        auto_fetch: bool,
    ) -> PyResult<(Vec<(String, String, String)>, Vec<(String, String)>)> {
        let ks: Vec<Crate> = crates
            .into_iter()
            .map(|(name, version)| Crate { name, version })
            .collect();
        let (found, missing) = resolve_all(&ks, auto_fetch);
        Ok((
            found
                .into_iter()
                .map(|s| {
                    (
                        s.krate.name,
                        s.krate.version,
                        s.src_path.to_string_lossy().into_owned(),
                    )
                })
                .collect(),
            missing.into_iter().map(|k| (k.name, k.version)).collect(),
        ))
    }

    #[pyfunction]
    fn rustc_version() -> &'static str {
        nacre_rustc_version()
    }

    #[pymodule]
    fn carpace(m: &Bound<'_, PyModule>) -> PyResult<()> {
        m.add_function(wrap_pyfunction!(build_stub, m)?)?;
        m.add_function(wrap_pyfunction!(probe, m)?)?;
        m.add_function(wrap_pyfunction!(resolve, m)?)?;
        m.add_function(wrap_pyfunction!(rustc_version, m)?)?;
        Ok(())
    }
}
