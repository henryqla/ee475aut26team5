package uw.edu.roadqualitymapper.data.local

import android.content.Context
import androidx.room.Database
import androidx.room.Room
import androidx.room.RoomDatabase

@Database(
    entities = [
        TripEntity::class,
        SegmentEntity::class,
        EventEntity::class,
        GpsFixEntity::class,
        DeviceMessageEntity::class,
    ],
    version = 1,
    exportSchema = false,
)
abstract class RoadDatabase : RoomDatabase() {
    abstract fun dao(): RoadDao

    companion object {
        @Volatile private var instance: RoadDatabase? = null

        /** The one database for the whole app. */
        fun get(context: Context): RoadDatabase = instance ?: synchronized(this) {
            instance ?: Room.databaseBuilder(context.applicationContext, RoadDatabase::class.java, "road.db")
                // While we're testing: if a new app version changes the tables, start the phone's
                // database over instead of crashing. This deletes anything not yet uploaded, so
                // write real migrations once drives on the phone matter.
                .fallbackToDestructiveMigration(dropAllTables = true)
                .build()
                .also { instance = it }
        }
    }
}
