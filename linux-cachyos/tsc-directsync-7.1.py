#!/usr/bin/env python3
from pathlib import Path
import re
import sys

root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(".")
touched = []

def die(msg):
    raise SystemExit(f"tsc-directsync patcher: {msg}")

def read(rel):
    p = root / rel
    if not p.exists():
        die(f"missing {rel}")
    return p.read_text()

def write(rel, data):
    p = root / rel
    old = p.read_text()
    if old != data:
        p.write_text(data)
        touched.append(rel)

def replace_once(data, old, new, rel, what):
    if new in data:
        return data
    if old not in data:
        die(f"{rel}: missing anchor for {what}")
    return data.replace(old, new, 1)

def regex_once(data, pattern, repl, rel, what, flags=0):
    if repl in data:
        return data
    new, n = re.subn(pattern, repl, data, count=1, flags=flags)
    if n != 1:
        die(f"{rel}: missing regex anchor for {what}")
    return new

# arch/x86/include/asm/tsc.h
rel = "arch/x86/include/asm/tsc.h"
s = read(rel)
if "extern int tsc_allow_direct_sync;" not in s:
    s = replace_once(
        s,
        "extern int tsc_clocksource_reliable;\n",
        "extern int tsc_clocksource_reliable;\nextern int tsc_allow_direct_sync;\n",
        rel,
        "tsc_allow_direct_sync extern",
    )
write(rel, s)

# arch/x86/kernel/tsc.c
rel = "arch/x86/kernel/tsc.c"
s = read(rel)

if "int tsc_allow_direct_sync;" not in s:
    s = replace_once(
        s,
        "int tsc_clocksource_reliable;\n",
        "int tsc_clocksource_reliable;\nint tsc_allow_direct_sync;\n",
        rel,
        "tsc_allow_direct_sync global",
    )

if '"directsync"' not in s:
    anchor = '\tif (!strcmp(str, "nowatchdog"))\n\t\ttsc_watchdog = TSC_WATCHDOG_OFF;\n'
    insert = anchor + '\tif (!strcmp(str, "directsync")) {\n\t\ttsc_allow_direct_sync = 1;\n\t\ttsc_watchdog = TSC_WATCHDOG_OFF;\n\t}\n'
    s = replace_once(s, anchor, insert, rel, "tsc=directsync setup")

write(rel, s)

# arch/x86/kernel/tsc_sync.c
rel = "arch/x86/kernel/tsc_sync.c"
s = read(rel)

old = """static inline unsigned int loop_timeout(int cpu)
{
	return (cpumask_weight(topology_core_cpumask(cpu)) > 1) ? 2 : 20;
}
"""
new = """static inline unsigned int loop_timeout(int cpu)
{
	if (!boot_cpu_has(X86_FEATURE_TSC_ADJUST) && tsc_allow_direct_sync)
		return 30;

	return (cpumask_weight(topology_core_cpumask(cpu)) > 1) ? 2 : 20;
}
"""
if "tsc_allow_direct_sync)\n\t\treturn 30;" not in s:
    s = replace_once(s, old, new, rel, "loop_timeout directsync extension")

if "atomic_set(&test_runs, 1000);" not in s:
    s = regex_once(
        s,
        r'\tif \(!boot_cpu_has\(X86_FEATURE_TSC_ADJUST\)\)\n'
        r'\t\tatomic_set\(&test_runs, 1\);\n'
        r'\telse\n'
        r'\t\tatomic_set\(&test_runs, 3\);',
        '\tif (boot_cpu_has(X86_FEATURE_TSC_ADJUST))\n'
        '\t\tatomic_set(&test_runs, 5);\n'
        '\telse if (tsc_allow_direct_sync)\n'
        '\t\tatomic_set(&test_runs, 1000);\n'
        '\telse\n'
        '\t\tatomic_set(&test_runs, 1);',
        rel,
        "test_runs directsync policy",
    )

if "(random_warps && !tsc_allow_direct_sync)" not in s:
    s = replace_once(
        s,
        "} else if (atomic_dec_and_test(&test_runs) || random_warps) {",
        "} else if (atomic_dec_and_test(&test_runs) ||\n\t\t   (random_warps && !tsc_allow_direct_sync)) {",
        rel,
        "random_warps directsync retry",
    )

if "write_tsc_adjustment(s64 adjustment)" not in s:
    marker = """/*
 * Freshly booted CPUs call into this:
 */
void check_tsc_sync_target(void)
"""
    helper = """static inline cycles_t write_tsc_adjustment(s64 adjustment)
{
	cycles_t adjval, nextval;

	rdmsrl(MSR_IA32_TSC, adjval);
	adjval += adjustment;
	wrmsrl(MSR_IA32_TSC, adjval);
	rdmsrl(MSR_IA32_TSC, nextval);

	return nextval - adjval;
}

"""
    s = replace_once(s, marker, helper + marker, rel, "write_tsc_adjustment helper")

if "gbl_max_warp, est_overhead = 0" not in s:
    s = replace_once(
        s,
        "\tcycles_t cur_max_warp, gbl_max_warp;\n",
        "\tcycles_t cur_max_warp, gbl_max_warp, est_overhead = 0;\n",
        rel,
        "est_overhead local",
    )

if "TSC direct sync: CPU%u observed" not in s:
    s = regex_once(
        s,
        r'\tcur->adjusted \+= cur_max_warp;\s*'
        r'pr_warn\("TSC ADJUST compensate: CPU%u observed %lld warp\. Adjust: %lld\\n",\s*'
        r'cpu, cur_max_warp, cur->adjusted\);\s*'
        r'wrmsr[ql]\(MSR_IA32_TSC_ADJUST, cur->adjusted\);\s*'
        r'goto retry;',
        '\tif (boot_cpu_has(X86_FEATURE_TSC_ADJUST)) {\n'
        '\t\tcur->adjusted += (s64)cur_max_warp + (s64)est_overhead;\n\n'
        '\t\tpr_warn("TSC ADJUST compensate: CPU%u observed %lld warp. Adjust: %lld\\\\n",\n'
        '\t\t\tcpu, cur_max_warp, cur->adjusted);\n\n'
        '\t\twrmsrq(MSR_IA32_TSC_ADJUST, cur->adjusted);\n'
        '\t} else if (tsc_allow_direct_sync) {\n'
        '\t\tpr_info("TSC direct sync: CPU%u observed %lld warp. Overhead: %lld\\\\n",\n'
        '\t\t\tcpu, cur_max_warp, est_overhead);\n'
        '\t\test_overhead = write_tsc_adjustment((s64)cur_max_warp + (s64)est_overhead);\n'
        '\t}\n'
        '\tgoto retry;',
        rel,
        "TSC_ADJUST/directsync adjustment block",
        flags=re.S,
    )

write(rel, s)

if touched:
    print("TSC directsync patcher touched:")
    for f in touched:
        print(f"  {f}")
else:
    print("TSC directsync patcher: already applied")
