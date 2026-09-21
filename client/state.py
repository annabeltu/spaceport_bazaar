"""Checks for the practice exercise's authoritative snapshots."""
from generated import bazaar_pb2 as pb


def require(condition, description):
    if not condition:
        raise RuntimeError(description)


def bundle(value):
    return value.water, value.food, value.components


def validate_initial(state):
    require(state.self_station_id == "P01", "Expected station P01.")
    require(bundle(getattr(state, "self").inventory) == (30, 30, 30), "Expected starting inventory (30, 30, 30).")
    require(getattr(state, "self").specialty == pb.RESOURCE_WATER, "Expected WATER specialty.")
    require(state.snapshot_sequence == 1, "Expected initial snapshot sequence 1.")
    # Versions 3 and 4 are supported reconnect points after steps 2 and 3.
    require(state.world_version in (2, 3, 4), "Expected starting world version 2 or a reconnect after step 2 or 3.")
    require(any(
        ad.station_id == "P02" and ad.status == pb.PUBLICATION_STATUS_ACTIVE
        and list(ad.selling.items) == [pb.RESOURCE_FOOD]
        and list(ad.seeking.items) == [pb.RESOURCE_WATER]
        for ad in state.advertisements.items
    ), "Expected P02's active food-for-water advertisement.")


def validate_progress(state, version, sequence):
    require(state.world_version == version, f"Expected world version {version}.")
    require(state.snapshot_sequence == sequence, f"Expected snapshot sequence {sequence}.")
    require(bundle(getattr(state, "self").inventory) == (30, 30, 30), "Expected unchanged inventory (30, 30, 30).")


def validate_advertisement(state, selling, seeking, object_id=None):
    listings = [ad for ad in state.advertisements.items
                if ad.station_id == state.self_station_id and ad.status == pb.PUBLICATION_STATUS_ACTIVE]
    require(len(listings) == 1, "Expected exactly one active P01 advertisement.")
    ad = listings[0]
    require(list(ad.selling.items) == selling and list(ad.seeking.items) == seeking
            and ad.expires_tick == 6, "Advertisement contents do not match.")
    require(object_id is None or ad.advertisement_id == object_id, "Advertisement ID does not match result.")
    return ad.advertisement_id


def validate_result(result, run_id, request_id):
    require(result.run_id == run_id and result.request_id == request_id
            and result.ok and result.code == pb.RESULT_CODE_OK, f"Command failed or result did not match: {result}")
    require(bool(result.object_id.value), "Successful command is missing its object ID.")
    return result.object_id.value


def validate_offer(state, offer_id):
    offers = [offer for offer in state.offers.items if offer.offer_id == offer_id]
    require(len(offers) == 1, "Expected the new offer in state.")
    offer = offers[0]
    require(offer.proposer_id == "P01" and offer.recipient_id == "P02"
            and bundle(offer.give) == (2, 0, 0) and bundle(offer.receive) == (0, 1, 0)
            and offer.expires_tick == 6 and offer.status == pb.OFFER_STATUS_OPEN,
            "Expected an open offer of two water for one food to P02.")
