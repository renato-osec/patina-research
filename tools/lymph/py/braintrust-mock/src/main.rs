use std::collections::HashMap;

struct Map {
  passthrough: bool,
  lookup: HashMap<u64, u64>,
}

impl Map {
  fn lookup_or_passthrough(&self, key: u64, passthrough_flag: bool) -> u64 {
      if self.passthrough == passthrough_flag {
          key
      } else {
          self.lookup[&key]
      }
  }

  fn new(passthrough: bool) -> Map {
      Map {
          passthrough: passthrough,
          lookup: HashMap::new(),
      }
  }
}

fn inv(x: bool) -> bool {
    !x
}

fn main() {
    let mut m = Map::new(true);
    let r = 3;
    let l = "hello";
    m.lookup.insert(1, r);
    let k = true;
    let s = m.lookup_or_passthrough(1, inv(k));
    println!("{}", s);
    println!("{}", l);
}
