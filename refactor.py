import re

with open("pages/admin.py", "r") as f:
    lines = f.readlines()

new_lines = []
skip_next = False

i = 0
while i < len(lines):
    line = lines[i]
    
    # Check for `colA, colB = st.columns(...)`
    if re.search(r'^\s*colA,\s*colB\s*=\s*st\.columns', line):
        i += 1
        continue
        
    # Check for `with colA:`
    match_cola = re.search(r'^(\s*)with colA:', line)
    if match_cola:
        indent = match_cola.group(1)
        # We need to change `with colA:` to `with st.expander("Nova [item]", expanded=False):`
        # Let's find the next line which is `st.markdown("### Nova ...")`
        next_line = lines[i+1]
        m_title = re.search(r'st\.markdown\("### (.*?)"\)', next_line)
        if m_title:
            title = m_title.group(1)
            new_lines.append(f'{indent}with st.expander("{title}", expanded=False):\n')
            i += 2 # skip `with colA:` and `st.markdown(...)`
            continue
        else:
            new_lines.append(f'{indent}with st.expander("Novo Registro", expanded=False):\n')
            i += 1
            continue

    # Clean empty lines before `with colB` if any
    
    # Check for `with colB:`
    match_colb = re.search(r'^(\s*)with colB:', line)
    if match_colb:
        indent_len = len(match_colb.group(1))
        # Now we process all subsequent lines that are indented more than `with colB:`
        i += 1
        while i < len(lines):
            sub_line = lines[i]
            if sub_line.strip() == "":
                new_lines.append(sub_line)
                i += 1
                continue
            
            # Check if indentation is deeper
            sub_indent = len(sub_line) - len(sub_line.lstrip())
            if sub_indent > indent_len:
                # remove 4 spaces
                if sub_line.startswith(" " * 4):
                    new_lines.append(sub_line[4:])
                else:
                    new_lines.append(sub_line)
                i += 1
            else:
                break
        continue
        
    new_lines.append(line)
    i += 1

with open("pages/admin.py", "w") as f:
    f.writelines(new_lines)
