# Orthopedics and sports medicine
Demand drivers (each a separate factor):
- Degenerative joint disease: adults 45-64 and 65+, arthritis prevalence, obesity (PLACES), Medicare share (joint
  replacement volume).
- Sports injuries: high schools with athletic programs (football, soccer, basketball; athletic trainers refer ACL,
  meniscus, shoulder and concussion cases), youth club sports fields and complexes, colleges, active adults 18-44,
  gyms and running/cycling communities.
- Work injuries: workers' comp volume follows industrial, construction, warehouse and energy employment (LODES jobs
  by industry), and employer occupational-health contracts.
- Trauma and fractures: proximity to ERs, trauma centers and urgent cares that send fracture follow-ups.
Referral sources: primary care density, urgent care centers, ER/trauma centers, high school athletic trainers,
physical therapists (two-way), chiropractors, employers.
Competition is not just other orthopedists:
- Independent orthopedic groups; hospital-employed orthopedics (Memorial Hermann, Houston Methodist, HCA, St. Luke's)
  whose outpatient sites and hospital campuses draw patients toward the system; orthopedic urgent care walk-ins;
  standalone PT chains (for the PT line); imaging centers (for MRI); ambulatory surgery centers (for surgery).
Hospitals: surgeons need short drives to the hospital(s) where they operate and round; large regional hospitals
also pull patients into their own clinics. Model both.
Service lines that change the site:
- X-ray: shielded room, almost always on site.
- MRI: about 600-1,000 sq ft shielded suite plus equipment room, ground floor or slab, heavy floor load, magnet
  delivery access and 3-phase power; capital cost in the millions, so a long horizon favors owning or a long lease
  with landlord-funded shielding. Competes with independent imaging centers.
- Physical therapy: 2,000-4,000 sq ft open gym; frequent visits (2-3 a week) make parking turnover and short
  drives matter more than visibility.
- Ambulatory surgery center: 8,000-15,000 sq ft, Medicare certification, separate entrance, generator and medical
  gas; heavy buildout favors buying or build-to-suit; low visibility need; mostly commercial and Medicare payers.
- Walk-in orthopedic urgent care: visibility from a busy road, signage, evening and weekend hours, retail frontage.
Space: 5-6 parking spaces per 1,000 sq ft, drop-off at the door, step-free entry for patients on crutches.
How an outpatient orthopedic clinic actually wins (use these mechanisms, not template factors):
- New patients mostly arrive by referral or a direct search after an injury; established patients return for
  follow-ups (fracture care every 2-6 weeks, post-op at 2 weeks, 6 weeks, 3 months). Repeat trips make drive time
  and parking count more than for one-off visits.
- The binding constraint is usually staffed rooms and imaging, not demand: about 3 exam rooms per provider, one
  X-ray room per 2-3 providers, casting space. A site with more demand than the clinic can see adds nothing.
- Growth that only moves patients from the practice's own offices is not growth: count net-new patients after
  subtracting what existing offices would lose (cannibalization), and compare against expanding an existing site.
- Payers decide who can come: Medicare and Medicare Advantage for joints and spine, commercial for sports and
  elective cases, workers' comp for occupational injuries, Medicaid/CHIP for children. A narrow network can close a
  market whose population looks ideal.
- A nearby orthopedic group is a competitor unless the intake documents a referral or call-coverage relationship.
  A hospital campus can be a referrer (ER fracture follow-ups) and a competitor (its employed orthopedists) at once.
Must keep:
- `hospital|asc|surgery center|operat` Drive time to the hospitals or surgery centers where the surgeons operate, and hospital systems whose employed orthopedists compete
- `compet|urgent care|imaging|hospital-employed|orthopedi` Competition beyond other orthopedists: hospital-employed groups, orthopedic urgent care, imaging, PT chains
- `primary care|referr|emergency|\ber\b|urgent care` Referral base: primary care, emergency departments and urgent cares that send fractures and new injuries
- `parking|step-free|accessib|drop-off|crutch|walker` Parking at the door and a step-free path to check-in for patients on crutches, walkers and in boots
