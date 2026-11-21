-- Knot Resolver 5 as a forwarder (forwarding-role cell): everything to the lab recursive upstream at .54.
net.listen('0.0.0.0', 53, { kind = 'dns' })
trust_anchors.remove('.')
policy.add(policy.all(policy.FORWARD('172.31.53.54')))
