"""Component counts use ownership leaves; joint references never add parts."""


def component_inventory(ir):
    joints={j['id'] for j in ir.get('joints',[])}
    source={c['id']:c for c in ir.get('components',[])}
    components={key:c for key,c in source.items() if key not in joints}
    composite=set()
    for component in components.values():
        parent=component.get('parent_id');seen=set()
        while parent in joints and parent not in seen:
            seen.add(parent);parent=source.get(parent,{}).get('parent_id')
        if parent in components:composite.add(parent)
    leaves=[key for key in components if key not in composite]
    return {'leaf':len(leaves),'composite':len(composite),'total':len(components),
            'joints':len(joints),'leaf_ids':leaves}
