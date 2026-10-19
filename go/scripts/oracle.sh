#!/bin/bash
# oracle.sh SCENARIO COMMAND — local parity check against the Python CLI.
# Ages two APFS clones of $ORACLE_SOURCE identically, applies the change with
# Python on one and Go ($GO_BIN) on the other, then requires identical trees
# and modes and a passing Python verify on the Go clone.
set -u
S="${ORACLE_WORK:-$(mktemp -d)}"
GO_BIN="${GO_BIN:-$S/x86qw}"
SOURCE="${ORACLE_SOURCE:-$HOME/Games/x86qw}"
scenario=$1; command=$2
if [ "$scenario" = fresh ]; then
  profile=$command
  : "${PYTHON_BUNDLE:?set PYTHON_BUNDLE to an extracted installer bundle}"
  rm -rf "$S/fresh-py" "$S/fresh-go" "$S/home"; mkdir -p "$S/home"
  (cd "$S" && HOME="$S/home" python3 -I "$PYTHON_BUNDLE/x86qw.pyz" --online-only install --non-interactive \
    --platform macos --channel stable --release latest --profile "$profile" "$S/fresh-py" < /dev/null > "$S/fresh-py.log" 2>&1)
  echo "python exit=$?"
  "$GO_BIN" install --profile "$profile" "$S/fresh-go" | tail -1
  diff -rq -x staging -x sessions -x cli -x x86qw.sh -x .DS_Store "$S/fresh-py" "$S/fresh-go" && echo "TREES IDENTICAL"
  exit
fi
age() {
  root=$1
  case $scenario in
    ktx-update)
      sed -i '' 's/^selection\t1.47+x86qw.19$/selection\t1.47+x86qw.18/' $root/.x86qw/components/ktx/receipt ;;
    ktx-modified)
      sed -i '' 's/^selection\t1.47+x86qw.19$/selection\t1.47+x86qw.18/' $root/.x86qw/components/ktx/receipt
      printf '\n// edição do usuário\n' >> $root/qw/x86qw-ktx-help.cfg ;;
    qtv-add)
      while IFS=$'\t' read -r path digest; do rm -f "$root/$path"; done < $root/.x86qw/components/qtv/inventory
      rm -rf $root/.x86qw/components/qtv
      find $root/qtv -type d -empty -delete 2>/dev/null ;;
    td2-update)
      sed -i '' 's/^selection\t2.22+x86qw.5$/selection\t2.22+x86qw.4/' $root/.x86qw/components/total-destruction-2/receipt
      rm -f $root/td2/x86qw_td2.dat ;;
  esac
}
for side in py go; do rm -rf $S/oracle-$side; cp -cR "$SOURCE" $S/oracle-$side; age $S/oracle-$side; done
(cd $S/oracle-py && ./x86qw.sh $command --yes < /dev/null > $S/oracle-py.log 2>&1); echo "python exit=$?"
"$GO_BIN" $command --yes $S/oracle-go > $S/oracle-go.log 2>&1; echo "go exit=$?"
tail -3 $S/oracle-go.log
diff -rq -x staging -x sessions -x .DS_Store -x x86qw.pyz $S/oracle-py $S/oracle-go > $S/oracle-diff.txt 2>&1
if [ -s $S/oracle-diff.txt ]; then echo "TREES DIFFER:"; head -20 $S/oracle-diff.txt; else echo "TREES IDENTICAL"; fi
modes() { (cd $1 && find . -path ./.x86qw/staging -prune -o -path ./.x86qw/sessions -prune -o ! -name .DS_Store -print0 | xargs -0 stat -f '%Sp %N' | sort); }
diff <(modes $S/oracle-py) <(modes $S/oracle-go) > $S/oracle-modes.txt && echo "MODES IDENTICAL" || { echo "MODES DIFFER:"; head -10 $S/oracle-modes.txt; }
(cd $S/oracle-go && ./x86qw.sh verify --json < /dev/null 2>&1 | tail -1)

