-- Knot Resolver 5, lab deviations only: listen on the lab net, no trust anchor, root hints.
net.listen('0.0.0.0', 53, { kind = 'dns' })
trust_anchors.remove('.')
modules.load('hints > iterate')
hints.root({ ['a.root.pdlab.'] = '172.31.53.10' })
