package uw.edu.roadqualitymapper.data

/** Longest error text shown on screen. */
private const val MAX_ERROR_LENGTH = 60

/**
 * An error short enough for a phone screen. Library errors put the URL, headers and request on
 * the lines after the first, so only the first line is kept.
 */
fun Throwable.shortMessage(): String {
    val firstLine = message?.lineSequence()?.firstOrNull { it.isNotBlank() }?.trim() ?: javaClass.simpleName
    return if (firstLine.length <= MAX_ERROR_LENGTH) firstLine else firstLine.take(MAX_ERROR_LENGTH - 1) + "…"
}
