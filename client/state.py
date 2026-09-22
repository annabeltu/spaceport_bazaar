"""Checks for the practice exercise's authoritative snapshots."""
# Step numbers refer to starter/README.md, "Complete the exchange".
# Each label applies to the entire function below it.

from generated import bazaar_pb2 as pb


# Shared by steps 1-10: stop when an expected condition is not met.
def require(condition, description):
    if not condition:
        raise RuntimeError(description)


# Shared state checks: read resource quantities as (water, food, components).
def bundle(value):
    return value.water, value.food, value.components


# Step 1: check the starting snapshot, including progress restored on reconnect.
def validate_initial(state):
    require(state.self_station_id == "P01", "Expected station P01.")
    require(state.world_version in range(2, 10),
            "Expected world version 2 through 9; this client supports steps 1 through 8.")
    inventory = ((30, 30, 30) if state.world_version <= 5 else
                 (28, 31, 30) if state.world_version <= 7 else (28, 31, 31))
    require(bundle(getattr(state, "self").inventory) == inventory,
            f"Expected inventory {inventory} at world version {state.world_version}.")
    require(getattr(state, "self").specialty == pb.RESOURCE_WATER, "Expected WATER specialty.")
    require(state.snapshot_sequence == 1, "Expected initial snapshot sequence 1.")
    require(any(
        ad.station_id == "P02" and ad.status == pb.PUBLICATION_STATUS_ACTIVE
        and list(ad.selling.items) == [pb.RESOURCE_FOOD]
        and list(ad.seeking.items) == [pb.RESOURCE_WATER]
        for ad in state.advertisements.items
    ), "Expected P02's active food-for-water advertisement.")
    if state.world_version == 9:
        validate_withdrawn(state)
    elif state.world_version >= 5:
        validate_advertisement(state, [], [pb.RESOURCE_COMPONENTS])
        offer_id = recover_offer_id(state)
        if state.world_version == 5:
            validate_offer(state, offer_id)
        elif state.world_version in (6, 7):
            validate_acceptance(state, offer_id)
            if state.world_version == 7:
                validate_gift(state)
        else:
            validate_completed(state)



# Steps 2-8 and 10: check world version, snapshot sequence, and inventory.
def validate_progress(state, version, sequence, inventory=(30, 30, 30)):
    require(state.world_version == version, f"Expected world version {version}.")
    require(state.snapshot_sequence == sequence, f"Expected snapshot sequence {sequence}.")
    require(bundle(getattr(state, "self").inventory) == inventory, f"Expected inventory {inventory}.")


# Steps 2-3 and 7, plus reconnect checks: verify the active advertisement.
def validate_advertisement(state, selling, seeking, object_id=None):
    listings = [ad for ad in state.advertisements.items
                if ad.station_id == state.self_station_id and ad.status == pb.PUBLICATION_STATUS_ACTIVE]
    require(len(listings) == 1, "Expected exactly one active P01 advertisement.")
    ad = listings[0]
    require(list(ad.selling.items) == selling and list(ad.seeking.items) == seeking
            and ad.expires_tick == 6, "Advertisement contents do not match.")
    require(object_id is None or ad.advertisement_id == object_id, "Advertisement ID does not match result.")
    return ad.advertisement_id


# Steps 2-4, 7-8, and 10: check successful command results and object IDs.
def validate_result(result, run_id, request_id):
    require(result.run_id == run_id and result.request_id == request_id
            and result.ok and result.code == pb.RESULT_CODE_OK, f"Command failed or result did not match: {result}")
    require(bool(result.object_id.value), "Successful command is missing its object ID.")
    return result.object_id.value


# Step 4: verify the open two-water-for-one-food offer.
def validate_offer(state, offer_id):
    offers = [offer for offer in state.offers.items if offer.offer_id == offer_id]
    require(len(offers) == 1, "Expected the new offer in state.")
    offer = offers[0]
    require(offer.proposer_id == "P01" and offer.recipient_id == "P02"
            and bundle(offer.give) == (2, 0, 0) and bundle(offer.receive) == (0, 1, 0)
            and offer.expires_tick == 6 and offer.status == pb.OFFER_STATUS_OPEN,
            "Expected an open offer of two water for one food to P02.")


# Step 5, rechecked in step 6: verify P02 accepted and the trade settled.
def validate_acceptance(state, offer_id):
    offers = [offer for offer in state.offers.items if offer.offer_id == offer_id]
    require(len(offers) == 1 and offers[0].status == pb.OFFER_STATUS_ACCEPTED,
            "Expected our offer to be accepted.")
    transactions = state.transactions.items
    require(len(transactions) == 1 and transactions[0].offer_id == offer_id,
            "Expected exactly one transaction for our offer.")
    require(bundle(getattr(state, "self").inventory) == (28, 31, 30),
            "Expected inventory (28, 31, 30) after P02 accepted.")


# Step 6 and before step 7: find the free component offer and save its ID.
def validate_gift(state):
    offers = [offer for offer in state.offers.items
              if offer.proposer_id == "P02" and offer.recipient_id == "P01"
              and offer.status == pb.OFFER_STATUS_OPEN
              and bundle(offer.give) == (0, 0, 1)
              and bundle(offer.receive) == (0, 0, 0)]
    require(len(offers) == 1 and bool(offers[0].offer_id),
            "Expected one open gift of one component from P02 to P01.")
    require(bundle(getattr(state, "self").inventory) == (28, 31, 30),
            "Expected inventory to remain (28, 31, 30) before accepting the gift.")
    return offers[0].offer_id


# Step 7: check the gift acceptance result and recover its transaction ID.
def validate_accept_result(result, run_id, request_id, offer_id):
    require(validate_result(result, run_id, request_id) == offer_id,
            "Acceptance result identifies a different offer.")
    require(bool(result.transaction_id.value),
            "Acceptance result is missing its transaction ID.")
    return result.transaction_id.value


# Step 7: verify the accepted gift, both transactions, and active advertisement.
def validate_gift_accepted(state, offer_id, transaction_id, advertisement_id):
    require(bundle(getattr(state, "self").inventory) == (28, 31, 31),
            "Expected inventory (28, 31, 31) after accepting the gift.")
    offers = [offer for offer in state.offers.items if offer.offer_id == offer_id]
    require(len(offers) == 1 and offers[0].status == pb.OFFER_STATUS_ACCEPTED
            and offers[0].transaction_id.value == transaction_id,
            "Expected the accepted gift linked to the result transaction.")
    transactions = state.transactions.items
    require(len(transactions) == 2, "Expected two transactions after accepting the gift.")
    matches = [tx for tx in transactions if tx.transaction_id == transaction_id]
    require(len(matches) == 1 and matches[0].offer_id == offer_id
            and matches[0].proposer_id == "P02" and matches[0].recipient_id == "P01"
            and bundle(matches[0].give) == (0, 0, 1)
            and bundle(matches[0].receive) == (0, 0, 0),
            "Expected the result transaction to transfer one component for free.")
    validate_advertisement(state, [], [pb.RESOURCE_COMPONENTS], advertisement_id)


# Reconnect support for steps 4-7: recover the existing offer instead of resending it.
def recover_offer_id(state):
    offers = [offer for offer in state.offers.items
              if offer.proposer_id == "P01" and offer.recipient_id == "P02"
              and bundle(offer.give) == (2, 0, 0)
              and bundle(offer.receive) == (0, 1, 0)]
    require(len(offers) == 1 and bool(offers[0].offer_id),
            "Expected our existing two-water-for-one-food offer.")
    return offers[0].offer_id


# Reconnect after step 7: verify the completed gift before proceeding to step 8.
def validate_completed(state):
    gifts = [offer for offer in state.offers.items
             if offer.proposer_id == "P02" and offer.recipient_id == "P01"
             and bundle(offer.give) == (0, 0, 1)
             and bundle(offer.receive) == (0, 0, 0)
             and offer.status == pb.OFFER_STATUS_ACCEPTED]
    require(len(gifts) == 1 and bool(gifts[0].transaction_id.value),
            "Expected the completed gift and its transaction.")
    advertisement_id = validate_advertisement(state, [], [pb.RESOURCE_COMPONENTS])
    validate_gift_accepted(state, gifts[0].offer_id,
                           gifts[0].transaction_id.value, advertisement_id)


# Steps 8 and 10, plus reconnect checks: verify removal and unchanged trades/inventory.
def validate_withdrawn(state, advertisement_id=None, previous_transactions=None):
    require(not any(ad.station_id == "P01" or
                    (advertisement_id is not None and ad.advertisement_id == advertisement_id)
                    for ad in state.advertisements.items),
            "Expected our advertisement to be absent after withdrawal.")
    require(bundle(getattr(state, "self").inventory) == (28, 31, 31),
            "Expected unchanged inventory (28, 31, 31) after withdrawal.")
    require(len(state.transactions.items) == 2,
            "Expected both completed transactions after withdrawal.")
    if previous_transactions is not None:
        require(state.transactions == previous_transactions,
                "Withdrawal changed the transaction history.")


# Step 9: verify the intentional request-limit error keeps the connection open.
def validate_request_capacity_error(error, run_id, request_id):
    require(error.code == pb.CONTROL_CODE_REQUEST_CAPACITY_EXCEEDED,
            "Expected the intentional request-capacity error.")
    require(error.run_id.value == run_id and error.request_id.value == request_id,
            "Request-capacity error belongs to another run or request.")
    require(not error.close_session, "Request-capacity error must leave the session open.")


# Step 10: check final state, stored results, trade totals, and zero simulation counters.
def validate_final(state, run_id, sequence, previous_transactions):
    require(state.run_id == run_id and state.self_station_id == "P01",
            "Final state belongs to another run or station.")
    validate_progress(state, 9, sequence, (28, 31, 31))
    validate_withdrawn(state, previous_transactions=previous_transactions)
    expected_requests = {
        "student-advertise-1", "student-advertise-seeking-1",
        "student-offer-1", "student-accept-1", "student-withdraw-1",
    }
    results = state.request_results.items
    require(len(results) == 5 and {r.request_id for r in results} == expected_requests,
            "Expected exactly the five successful stored requests, excluding the rejected request.")
    for result in results:
        validate_result(result, run_id, result.request_id)
    station = getattr(state, "self")
    require(bundle(station.imported_total) == (0, 1, 1)
            and bundle(station.exported_total) == (2, 0, 0),
            "Final import/export totals do not match the two trades.")
    require(state.tick == 0, "Expected no simulation tick.")
    for field in ("last_production", "last_unmet_upkeep", "produced_total",
                  "consumed_total", "unmet_total"):
        require(bundle(getattr(station, field)) == (0, 0, 0),
                f"Expected zero {field}.")
    for field in ("fully_supplied_ticks", "shortage_ticks",
                  "current_shortage_streak", "longest_shortage_streak"):
        require(getattr(station, field) == 0, f"Expected zero {field}.")
